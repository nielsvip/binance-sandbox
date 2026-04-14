#!/usr/bin/env python3
"""Bitget fresh trader scan: discover top traders, pull trades, analyze, match to klines.

Reuses auth/request infrastructure from bitget_trader_scraper.py.
Output: data/reverse_engineered/bitget_fresh_scan.json
"""
import json
import logging
import sys
import time
import numpy as np
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bitget_trader_scraper import (
    load_credentials,
    _request,
    _normalize_trader,
    _parse_order,
    _safe_float,
    _clean_symbol,
    _ts_to_dt,
    discover_traders_from_file,
    DATA_DIR,
)
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
KLINES_DIR = BASE_PATH / "klines_cache"
OUTPUT_PATH = BASE_PATH / "data" / "reverse_engineered" / "bitget_fresh_scan.json"

logger = logging.getLogger("bitget_fresh_scan")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(h)

RATE_DELAY = 0.5  # 2 req/sec as requested


def discover_top_traders(creds, top_n=30):
    """Try multiple V2 endpoints to discover traders."""
    all_traders = []
    seen = set()
    # Method 1: broker endpoint
    for endpoint in [
        "/api/v2/copy/mix-broker/query-traders",
        "/api/v2/copy/mix-trader/query-traders",
    ]:
        logger.info(f"Trying discovery endpoint: {endpoint}")
        for page_no in range(1, 4):  # up to 3 pages
            params = {"pageSize": "20", "pageNo": str(page_no)}
            time.sleep(RATE_DELAY)
            data = _request("GET", endpoint, params=params, creds=creds)
            if not data:
                logger.warning(f"  {endpoint} returned None (page {page_no})")
                break
            traders_raw = data if isinstance(data, list) else data.get("traderList", data.get("list", []))
            if not traders_raw:
                logger.warning(f"  {endpoint} returned empty list (page {page_no})")
                break
            for t in traders_raw:
                tid = t.get("traderId", t.get("traderUid", t.get("uid", t.get("encUid", ""))))
                if tid and tid not in seen:
                    seen.add(tid)
                    all_traders.append(_normalize_trader(t))
            logger.info(f"  {endpoint} page {page_no}: got {len(traders_raw)} traders (total unique: {len(all_traders)})")
            if len(traders_raw) < 20:
                break
        if len(all_traders) >= top_n:
            break
    # Method 2: follower endpoint
    if len(all_traders) < top_n:
        logger.info("Trying follower endpoint...")
        time.sleep(RATE_DELAY)
        data = _request("GET", "/api/v2/copy/mix-follower/query-traders", creds=creds)
        if data:
            traders_raw = data if isinstance(data, list) else data.get("traderList", data.get("list", []))
            for t in traders_raw:
                tid = t.get("traderId", t.get("traderUid", ""))
                if tid and tid not in seen:
                    seen.add(tid)
                    all_traders.append(_normalize_trader(t))
            logger.info(f"  Follower endpoint: {len(traders_raw)} traders")
    # Method 3: cache fallback
    if not all_traders:
        logger.info("All API methods failed, using cached traders...")
        all_traders = discover_traders_from_file()
    return all_traders[:top_n]


def pull_trade_history(creds, trader_id, days_back=90):
    """Pull closed trades for a trader. Try broker then trader endpoint."""
    all_trades = []
    end_time = str(int(time.time() * 1000))
    start_time = str(int((time.time() - days_back * 86400) * 1000))
    for endpoint in [
        "/api/v2/copy/mix-broker/query-history-traces",
        "/api/v2/copy/mix-trader/query-history-traces",
    ]:
        cursor = None
        batch_trades = []
        for batch in range(50):
            params = {
                "traderId": trader_id,
                "productType": "USDT-FUTURES",
                "startTime": start_time,
                "endTime": end_time,
                "limit": "100",
            }
            if cursor:
                params["idLessThan"] = cursor
            time.sleep(RATE_DELAY)
            data = _request("GET", endpoint, params=params, creds=creds)
            if not data:
                break
            orders = data if isinstance(data, list) else data.get("trackingList", data.get("list", data.get("traceList", [])))
            if not orders:
                break
            for o in orders:
                trade = _parse_order(o, trader_id)
                if trade:
                    batch_trades.append(trade)
            last_id = orders[-1].get("trackingNo", orders[-1].get("orderId", orders[-1].get("traceId", "")))
            if last_id and last_id != cursor:
                cursor = last_id
            else:
                break
            if len(orders) < 100:
                break
        if batch_trades:
            all_trades = batch_trades
            logger.info(f"  {endpoint}: {len(all_trades)} trades for {trader_id[:12]}..")
            break
        else:
            logger.info(f"  {endpoint}: 0 trades for {trader_id[:12]}..")
    return all_trades


def analyze_trader(trader_info, trades):
    """Compute detailed stats for a trader."""
    if not trades:
        return None
    total = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    breakeven = [t for t in trades if t["pnl"] == 0]
    wr = len(wins) / total * 100 if total > 0 else 0
    total_pnl = sum(t["pnl"] for t in trades)
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0
    pf = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else 999
    # Side analysis
    longs = [t for t in trades if t["side"] == "LONG"]
    shorts = [t for t in trades if t["side"] == "SHORT"]
    long_wr = len([t for t in longs if t["pnl"] > 0]) / len(longs) * 100 if longs else 0
    short_wr = len([t for t in shorts if t["pnl"] > 0]) / len(shorts) * 100 if shorts else 0
    # Symbol diversity
    symbols = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in trades:
        s = t["symbol"]
        symbols[s]["count"] += 1
        symbols[s]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            symbols[s]["wins"] += 1
    symbol_stats = {}
    for s, d in symbols.items():
        symbol_stats[s] = {
            "count": d["count"],
            "pnl": round(d["pnl"], 2),
            "wr": round(d["wins"] / d["count"] * 100, 1) if d["count"] > 0 else 0,
        }
    # Hold time analysis
    hold_times = []
    for t in trades:
        if t.get("entry_time") and t.get("exit_time"):
            et = t["entry_time"]
            xt = t["exit_time"]
            if isinstance(et, str):
                try:
                    et = datetime.fromisoformat(et.replace("Z", "+00:00"))
                except Exception:
                    continue
            if isinstance(xt, str):
                try:
                    xt = datetime.fromisoformat(xt.replace("Z", "+00:00"))
                except Exception:
                    continue
            if hasattr(et, "timestamp") and hasattr(xt, "timestamp"):
                delta_h = (xt.timestamp() - et.timestamp()) / 3600
                if 0 < delta_h < 10000:
                    hold_times.append(delta_h)
    avg_hold_h = np.mean(hold_times) if hold_times else 0
    median_hold_h = np.median(hold_times) if hold_times else 0
    # Timing analysis (hour of entry)
    entry_hours = defaultdict(lambda: {"count": 0, "pnl": 0})
    for t in trades:
        et = t.get("entry_time")
        if isinstance(et, str):
            try:
                et = datetime.fromisoformat(et.replace("Z", "+00:00"))
            except Exception:
                continue
        if hasattr(et, "hour"):
            h = et.hour
            entry_hours[h]["count"] += 1
            entry_hours[h]["pnl"] += t["pnl"]
    best_hours = sorted(entry_hours.items(), key=lambda x: x[1]["pnl"], reverse=True)[:3]
    # Leverage stats
    leverages = [t["leverage"] for t in trades if t["leverage"] > 0]
    avg_leverage = np.mean(leverages) if leverages else 0
    return {
        "trader_id": trader_info.get("trader_id", ""),
        "nickname": trader_info.get("nickname", ""),
        "api_win_rate": trader_info.get("win_rate", 0),
        "api_roi": trader_info.get("roi", 0),
        "api_total_pnl": trader_info.get("total_pnl", 0),
        "api_follower_count": trader_info.get("follower_count", 0),
        "api_total_orders": trader_info.get("total_orders", 0),
        "computed_total_trades": total,
        "computed_wr": round(wr, 2),
        "computed_total_pnl": round(total_pnl, 2),
        "computed_avg_win": round(float(avg_win), 2),
        "computed_avg_loss": round(float(avg_loss), 2),
        "computed_profit_factor": round(float(pf), 2),
        "long_count": len(longs),
        "short_count": len(shorts),
        "long_wr": round(long_wr, 2),
        "short_wr": round(short_wr, 2),
        "unique_symbols": len(symbols),
        "symbol_stats": dict(sorted(symbol_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)),
        "avg_hold_hours": round(float(avg_hold_h), 2),
        "median_hold_hours": round(float(median_hold_h), 2),
        "avg_leverage": round(float(avg_leverage), 1),
        "best_entry_hours_utc": [{"hour": h, "count": d["count"], "pnl": round(d["pnl"], 2)} for h, d in best_hours],
        "breakeven_count": len(breakeven),
        "short_bias": len(shorts) > len(longs),
        "short_outperforms": short_wr > long_wr,
    }


def load_klines(symbol):
    """Load klines for a symbol from local cache. Try 15m, then 1h."""
    for tf in ["15m", "1h", "4h"]:
        path = KLINES_DIR / f"{symbol}_{tf}.json"
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            if not data or len(data) < 50:
                continue
            return data, tf
        except Exception:
            continue
    return None, None


def match_trades_to_klines(trades):
    """For trades with entry_price>0, find matching kline context."""
    matched = []
    klines_cache = {}
    for t in trades:
        if t["entry_price"] <= 0 or t["exit_price"] <= 0:
            continue
        sym = t["symbol"]
        if sym not in klines_cache:
            kl, ktf = load_klines(sym)
            klines_cache[sym] = (kl, ktf)
        klines, tf = klines_cache[sym]
        if not klines or len(klines) < 50:
            continue
        # Find entry bar — klines are dicts with 'timestamp' key
        et = t.get("entry_time")
        if isinstance(et, str):
            try:
                et = datetime.fromisoformat(et.replace("Z", "+00:00"))
            except Exception:
                continue
        if not hasattr(et, "timestamp"):
            continue
        entry_ts_str = et.isoformat()
        # Parse kline timestamps and find closest
        kline_times = []
        for k in klines:
            ts_raw = k.get("timestamp", "")
            try:
                kline_times.append(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp())
            except Exception:
                kline_times.append(0)
        kline_times = np.array(kline_times)
        entry_epoch = et.timestamp()
        idx = np.searchsorted(kline_times, entry_epoch) - 1
        if idx < 20 or idx >= len(klines):
            continue
        # Compute basic indicators at entry
        closes = np.array([float(klines[i]["close"]) for i in range(max(0, idx - 50), idx + 1)])
        volumes = np.array([float(klines[i]["volume"]) for i in range(max(0, idx - 50), idx + 1)])
        if len(closes) < 20:
            continue
        # ADX approximation (14-period)
        highs = np.array([float(klines[i]["high"]) for i in range(max(0, idx - 50), idx + 1)])
        lows = np.array([float(klines[i]["low"]) for i in range(max(0, idx - 50), idx + 1)])
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        atr14 = np.mean(tr[-14:]) if len(tr) >= 14 else 0
        atr_pct = atr14 / closes[-1] * 100 if closes[-1] > 0 else 0
        # Simple trend: SMA20 slope
        sma20 = np.mean(closes[-20:])
        sma10 = np.mean(closes[-10:])
        trend = "UP" if sma10 > sma20 else "DOWN"
        # Volume relative to average
        vol_ratio = volumes[-1] / np.mean(volumes[-20:]) if np.mean(volumes[-20:]) > 0 else 1
        # RSI 14
        deltas = np.diff(closes[-15:])
        gains = np.where(deltas > 0, deltas, 0)
        losses_arr = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss_rsi = np.mean(losses_arr) if len(losses_arr) > 0 else 0
        rsi = 100 - (100 / (1 + avg_gain / avg_loss_rsi)) if avg_loss_rsi > 0 else 50
        matched.append({
            "symbol": sym,
            "side": t["side"],
            "pnl": t["pnl"],
            "won": t["pnl"] > 0,
            "atr_pct": round(atr_pct, 3),
            "trend": trend,
            "vol_ratio": round(float(vol_ratio), 2),
            "rsi_14": round(float(rsi), 1),
            "leverage": t["leverage"],
        })
    return matched


def find_indicator_patterns(matched_trades):
    """Find indicator patterns among winning vs losing trades."""
    if len(matched_trades) < 10:
        return {"note": "too few matched trades for pattern analysis"}
    wins = [t for t in matched_trades if t["won"]]
    losses = [t for t in matched_trades if not t["won"]]
    patterns = {}
    # ADX/ATR analysis: low vs high volatility
    low_atr = [t for t in matched_trades if t["atr_pct"] < 1.5]
    high_atr = [t for t in matched_trades if t["atr_pct"] >= 1.5]
    low_atr_wr = len([t for t in low_atr if t["won"]]) / len(low_atr) * 100 if low_atr else 0
    high_atr_wr = len([t for t in high_atr if t["won"]]) / len(high_atr) * 100 if high_atr else 0
    patterns["low_atr_wr"] = round(low_atr_wr, 1)
    patterns["high_atr_wr"] = round(high_atr_wr, 1)
    patterns["low_atr_count"] = len(low_atr)
    patterns["high_atr_count"] = len(high_atr)
    # Trend alignment
    with_trend = [t for t in matched_trades if (t["side"] == "LONG" and t["trend"] == "UP") or (t["side"] == "SHORT" and t["trend"] == "DOWN")]
    against_trend = [t for t in matched_trades if t not in with_trend]
    patterns["with_trend_wr"] = round(len([t for t in with_trend if t["won"]]) / len(with_trend) * 100, 1) if with_trend else 0
    patterns["against_trend_wr"] = round(len([t for t in against_trend if t["won"]]) / len(against_trend) * 100, 1) if against_trend else 0
    patterns["with_trend_count"] = len(with_trend)
    patterns["against_trend_count"] = len(against_trend)
    # RSI zones
    oversold = [t for t in matched_trades if t["rsi_14"] < 30]
    overbought = [t for t in matched_trades if t["rsi_14"] > 70]
    neutral = [t for t in matched_trades if 30 <= t["rsi_14"] <= 70]
    patterns["rsi_oversold_wr"] = round(len([t for t in oversold if t["won"]]) / len(oversold) * 100, 1) if oversold else 0
    patterns["rsi_overbought_wr"] = round(len([t for t in overbought if t["won"]]) / len(overbought) * 100, 1) if overbought else 0
    patterns["rsi_neutral_wr"] = round(len([t for t in neutral if t["won"]]) / len(neutral) * 100, 1) if neutral else 0
    # Short bias analysis
    short_wins = [t for t in matched_trades if t["side"] == "SHORT" and t["won"]]
    long_wins = [t for t in matched_trades if t["side"] == "LONG" and t["won"]]
    short_total = [t for t in matched_trades if t["side"] == "SHORT"]
    long_total = [t for t in matched_trades if t["side"] == "LONG"]
    patterns["short_wr_matched"] = round(len(short_wins) / len(short_total) * 100, 1) if short_total else 0
    patterns["long_wr_matched"] = round(len(long_wins) / len(long_total) * 100, 1) if long_total else 0
    patterns["short_count_matched"] = len(short_total)
    patterns["long_count_matched"] = len(long_total)
    # Volume spike analysis
    high_vol = [t for t in matched_trades if t["vol_ratio"] > 1.5]
    low_vol = [t for t in matched_trades if t["vol_ratio"] <= 1.5]
    patterns["high_volume_wr"] = round(len([t for t in high_vol if t["won"]]) / len(high_vol) * 100, 1) if high_vol else 0
    patterns["low_volume_wr"] = round(len([t for t in low_vol if t["won"]]) / len(low_vol) * 100, 1) if low_vol else 0
    return patterns


def main():
    logger.info("=" * 60)
    logger.info("BITGET FRESH TRADER SCAN")
    logger.info("=" * 60)
    creds = load_credentials()
    logger.info("Credentials loaded")
    # Step 1: Discover traders
    logger.info("\n--- STEP 1: Discover top traders ---")
    traders = discover_top_traders(creds, top_n=30)
    if not traders:
        logger.error("No traders discovered. Exiting.")
        sys.exit(1)
    logger.info(f"Discovered {len(traders)} traders")
    # Save discovered traders
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "discovered_traders.json", "w") as f:
        json.dump(traders, f, indent=2, default=str)
    # Step 2: Pull trades for each
    logger.info("\n--- STEP 2: Pull trade histories ---")
    all_analyses = []
    all_matched_trades = []
    for i, trader in enumerate(traders):
        tid = trader["trader_id"]
        nick = trader.get("nickname", "")
        logger.info(f"\n[{i+1}/{len(traders)}] Pulling trades for {nick} ({tid[:12]}..)")
        trades = pull_trade_history(creds, tid, days_back=90)
        if not trades:
            logger.info(f"  No trades returned for {nick}")
            continue
        # Filter out zero-price trades
        valid_trades = [t for t in trades if t.get("entry_price", 0) > 0 or t.get("pnl", 0) != 0]
        logger.info(f"  {len(trades)} total, {len(valid_trades)} with valid data")
        # Analyze
        analysis = analyze_trader(trader, valid_trades if valid_trades else trades)
        if analysis:
            all_analyses.append(analysis)
            logger.info(f"  WR={analysis['computed_wr']}% PnL=${analysis['computed_total_pnl']} PF={analysis['computed_profit_factor']} Syms={analysis['unique_symbols']} L/S={analysis['long_count']}/{analysis['short_count']}")
        # Match to klines
        matched = match_trades_to_klines(valid_trades)
        all_matched_trades.extend(matched)
        logger.info(f"  Matched {len(matched)} trades to local klines")
    # Step 3: Filter for best traders
    logger.info("\n--- STEP 3: Filter best traders ---")
    best = [a for a in all_analyses if a["computed_wr"] >= 60 and a["unique_symbols"] >= 3 and a["computed_total_trades"] >= 10]
    best.sort(key=lambda x: x["computed_profit_factor"], reverse=True)
    logger.info(f"Traders with WR>=60% AND >=3 symbols AND >=10 trades: {len(best)}")
    for b in best:
        logger.info(f"  {b['nickname']:20s} WR={b['computed_wr']}% PF={b['computed_profit_factor']} Trades={b['computed_total_trades']} Syms={b['unique_symbols']} Short%={b['short_count']/max(1,b['computed_total_trades'])*100:.0f}% ShortWR={b['short_wr']}%")
    # Short-bias traders
    short_bias = [a for a in best if a.get("short_outperforms") or a.get("short_wr", 0) > 65]
    logger.info(f"\nShort-side outperformers (WR>65% short OR short>long): {len(short_bias)}")
    for s in short_bias:
        logger.info(f"  {s['nickname']:20s} ShortWR={s['short_wr']}% LongWR={s['long_wr']}% ShortCount={s['short_count']}")
    # Step 4: Indicator patterns
    logger.info("\n--- STEP 4: Indicator pattern analysis ---")
    patterns = find_indicator_patterns(all_matched_trades)
    logger.info(f"Matched {len(all_matched_trades)} trades to klines for pattern analysis")
    for k, v in patterns.items():
        logger.info(f"  {k}: {v}")
    # Step 5: Save results
    result = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "traders_discovered": len(traders),
        "traders_with_trades": len(all_analyses),
        "traders_passing_filter": len(best),
        "total_matched_to_klines": len(all_matched_trades),
        "all_trader_analyses": all_analyses,
        "best_traders": best,
        "short_bias_traders": short_bias,
        "indicator_patterns": patterns,
        "summary": {
            "overall_short_wr": round(np.mean([a["short_wr"] for a in all_analyses if a["short_count"] > 5]), 1) if any(a["short_count"] > 5 for a in all_analyses) else 0,
            "overall_long_wr": round(np.mean([a["long_wr"] for a in all_analyses if a["long_count"] > 5]), 1) if any(a["long_count"] > 5 for a in all_analyses) else 0,
            "avg_hold_hours": round(float(np.mean([a["avg_hold_hours"] for a in all_analyses if a["avg_hold_hours"] > 0])), 1) if any(a["avg_hold_hours"] > 0 for a in all_analyses) else 0,
            "avg_leverage": round(float(np.mean([a["avg_leverage"] for a in all_analyses if a["avg_leverage"] > 0])), 1) if any(a["avg_leverage"] > 0 for a in all_analyses) else 0,
            "most_traded_symbols": dict(sorted(
                {sym: sum(1 for a in all_analyses for s, d in a["symbol_stats"].items() if s == sym)
                 for sym in set(s for a in all_analyses for s in a["symbol_stats"])}.items(),
                key=lambda x: x[1], reverse=True
            )[:15]),
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info(f"\nResults saved to {OUTPUT_PATH}")
    logger.info(f"Discovered: {len(traders)} | With trades: {len(all_analyses)} | Best: {len(best)} | Short-bias: {len(short_bias)}")


if __name__ == "__main__":
    main()
