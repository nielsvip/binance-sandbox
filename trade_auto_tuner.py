#!/usr/bin/env python3
"""Trade Auto-Tuner: 24/7 daemon that monitors live trades, analyzes indicator conditions, and recommends config adjustments to push toward 90%+ win rate."""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config as cfg

CFG = cfg.Config()
BASE_PATH = CFG.BASE_PATH
DATA_DIR = BASE_PATH / "data"
DECISIONS_DIR = DATA_DIR / "decisions"
TUNER_DIR = DATA_DIR / "auto_tuner"
LOG_DIR = Path.home() / "logs"
RECOMMENDED_FILE = TUNER_DIR / "recommended_config.json"
HISTORY_FILE = TUNER_DIR / "adjustment_history.jsonl"
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + STOCK_ACCOUNTS
MIN_TRADES_FOR_SIGNIFICANCE = 10
SAFETY_SCORE_BOUND = 10.0
SAFETY_THRESHOLD_PCT = 0.30
PROTECTED_PARAMS = {"STRICT_NO_LOSS", "STRICT_NO_LOSS_ACCOUNTS", "NOLOSS_MIN_PROFIT_PCT", "HEDGE_MODE", "HEDGE_ACCOUNTS", "HEDGE_TRIGGER_LOSS_PCT", "HEDGE_OVERSIZE_RATIO", "HEDGE_MAX_RATIO", "MAX_POSITION_SIZE", "MAX_ORDER_VALUE", "START_POSITION_SIZE"}
logger = logging.getLogger("trade_auto_tuner")
logger.setLevel(logging.DEBUG)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(TUNER_DIR, exist_ok=True)
_fh = RotatingFileHandler(str(LOG_DIR / "trade_auto_tuner.log"), maxBytes=50 * 1024 * 1024, backupCount=5)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_ch)


def _parse_timestamp(ts_str):
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def _classify_action(action):
    action = (action or "").upper()
    if "OPEN" in action or action in ("BUY", "SELL"):
        return "OPEN"
    if "CLOSE" in action or "REDUCE" in action:
        return "CLOSE"
    return "OTHER"


def _extract_position_key_base(pk):
    parts = pk.rsplit("_", 1)
    if len(parts) == 2 and parts[1] in ("LONG", "SHORT"):
        return parts[0], parts[1]
    return pk, "UNKNOWN"


def _bucket_value(val, buckets):
    for label, lo, hi in buckets:
        if lo <= val < hi:
            return label
    return "UNKNOWN"


WT_PERCENTILE_BUCKETS = [("<10", -999, 10), ("10-25", 10, 25), ("25-50", 25, 50), ("50-75", 50, 75), ("75-90", 75, 90), (">90", 90, 999)]
ADX_BUCKETS = [("<20_ranging", -999, 20), ("20-40_mild", 20, 40), (">40_strong", 40, 999)]
RSI_BUCKETS = [("<30", -999, 30), ("30-50", 30, 50), ("50-70", 50, 70), (">70", 70, 999)]
STOCHRSI_BUCKETS = [("<20", -999, 20), ("20-40", 20, 40), ("40-60", 40, 60), ("60-80", 60, 80), (">80", 80, 999)]
VOLRATIO_BUCKETS = [("<0.8", -999, 0.8), ("0.8-1.2", 0.8, 1.2), ("1.2-2.0", 1.2, 2.0), (">2.0", 2.0, 999)]
HOUR_BUCKETS = [("00-04", 0, 4), ("04-08", 4, 8), ("08-12", 8, 12), ("12-16", 12, 16), ("16-20", 16, 20), ("20-24", 20, 24)]
DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _extract_indicator_conditions(trade):
    conditions = {}
    snap = trade.get("snapshot", {})
    reason = trade.get("reason", "")
    wt1 = snap.get("wt1_15m")
    wt2 = snap.get("wt2_15m")
    if wt1 is not None:
        if wt1 > 60:
            conditions["wt_momentum"] = "IMPULSE_UP" if wt1 > wt2 else "EXHAUST_UP"
        elif wt1 < -60:
            conditions["wt_momentum"] = "IMPULSE_DOWN" if wt1 < wt2 else "EXHAUST_DOWN"
        else:
            conditions["wt_momentum"] = "NEUTRAL"
    if wt1 is not None and wt2 is not None:
        cross_val = wt1 - wt2
        if abs(cross_val) < 5:
            conditions["wt_cross"] = "BULL" if cross_val > 0 else "BEAR"
        else:
            conditions["wt_cross"] = "None"
        conditions["wt_cross_rising"] = str(wt1 > wt2)
    if wt1 is not None:
        conditions["wt_percentile"] = _bucket_value(abs(wt1), WT_PERCENTILE_BUCKETS)
    for div_type in ["BULL_DIV", "BEAR_DIV", "HIDDEN_BULL", "HIDDEN_BEAR"]:
        if div_type in reason:
            conditions["wt_divergence"] = div_type
            break
    if "wt_divergence" not in conditions:
        conditions["wt_divergence"] = "None"
    for struct in ["HH", "HL", "LH", "LL"]:
        if f"_{struct}_" in reason or reason.endswith(f"_{struct}"):
            conditions["wt_structure"] = struct
            break
    if "wt_structure" not in conditions:
        conditions["wt_structure"] = "UNKNOWN"
    adx = snap.get("adx_1h") or snap.get("adx")
    if adx is not None:
        conditions["adx_bucket"] = _bucket_value(adx, ADX_BUCKETS)
    rsi = snap.get("rsi_1h") or snap.get("rsi_15m") or snap.get("rsi")
    if rsi is not None:
        conditions["rsi_bucket"] = _bucket_value(rsi, RSI_BUCKETS)
    k_15 = snap.get("k_15m")
    if k_15 is not None:
        conditions["stochrsi_bucket"] = _bucket_value(k_15, STOCHRSI_BUCKETS)
    vol_ratio = snap.get("rel_vol") or snap.get("volume_ratio")
    if vol_ratio is not None:
        conditions["volratio_bucket"] = _bucket_value(vol_ratio, VOLRATIO_BUCKETS)
    ts = _parse_timestamp(trade.get("timestamp", ""))
    if ts:
        conditions["time_bucket"] = _bucket_value(ts.hour, HOUR_BUCKETS)
        conditions["day_of_week"] = DOW_NAMES[ts.weekday()]
    ha_3m = snap.get("ha_3m")
    ha_15m = snap.get("ha_15m")
    if ha_3m:
        conditions["ha_3m"] = ha_3m
    if ha_15m:
        conditions["ha_15m"] = ha_15m
    return conditions


def load_decisions(days_back=1, accounts=None):
    accounts = accounts or ALL_ACCOUNTS
    trades = []
    now = datetime.now(timezone.utc)
    for day_offset in range(days_back):
        date = now - timedelta(days=day_offset)
        date_str = date.strftime("%Y%m%d")
        for acct in accounts:
            fpath = DECISIONS_DIR / f"decisions_{acct}_{date_str}.jsonl"
            if not fpath.exists():
                continue
            try:
                with open(fpath, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            trade = json.loads(line)
                            trade["_account"] = acct
                            trade["_is_crypto"] = acct in CRYPTO_ACCOUNTS
                            trades.append(trade)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning(f"Failed to read {fpath}: {e}")
    return trades


def match_opens_and_closes(trades):
    trades_sorted = sorted(trades, key=lambda t: t.get("timestamp", ""))
    open_positions = {}
    completed = []
    for t in trades_sorted:
        action_type = _classify_action(t.get("action", ""))
        pk = t.get("position_key", "")
        if not pk:
            continue
        if action_type == "OPEN":
            open_positions[pk] = t
        elif action_type == "CLOSE" and pk in open_positions:
            entry = open_positions[pk]
            entry_price = entry.get("snapshot", {}).get("price", 0)
            exit_price = t.get("snapshot", {}).get("price", 0)
            _, side = _extract_position_key_base(pk)
            if entry_price and exit_price:
                if side == "LONG":
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - exit_price) / entry_price * 100
            else:
                pnl_pct = t.get("pnl_pct") or t.get("extra", {}).get("pnl_pct", 0)
            entry_ts = _parse_timestamp(entry.get("timestamp", ""))
            exit_ts = _parse_timestamp(t.get("timestamp", ""))
            hold_seconds = (exit_ts - entry_ts).total_seconds() if entry_ts and exit_ts else 0
            completed.append({"position_key": pk, "account": entry.get("_account", ""), "is_crypto": entry.get("_is_crypto", True), "side": side, "entry_price": entry_price, "exit_price": exit_price, "pnl_pct": pnl_pct, "is_win": pnl_pct > 0, "hold_seconds": hold_seconds, "entry_time": entry.get("timestamp", ""), "exit_time": t.get("timestamp", ""), "entry_reason": entry.get("reason", ""), "exit_reason": t.get("reason", ""), "conditions": _extract_indicator_conditions(entry), "entry_snapshot": entry.get("snapshot", {}), "exit_snapshot": t.get("snapshot", {})})
            del open_positions[pk]
    return completed


def compute_condition_stats(completed_trades):
    stats = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": []})
    for trade in completed_trades:
        conditions = trade.get("conditions", {})
        for cond_name, cond_val in conditions.items():
            key = f"{cond_name}={cond_val}"
            stats[key]["trades"].append(trade)
            if trade["is_win"]:
                stats[key]["wins"] += 1
            else:
                stats[key]["losses"] += 1
            stats[key]["total_pnl"] += trade.get("pnl_pct", 0)
    for cond_key, s in stats.items():
        total = s["wins"] + s["losses"]
        s["win_rate"] = (s["wins"] / total * 100) if total > 0 else 0
        s["avg_pnl"] = (s["total_pnl"] / total) if total > 0 else 0
        s["trade_count"] = total
    combo_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0, "trade_count": 0})
    important_combos = [("wt_momentum", "wt_divergence"), ("wt_cross", "adx_bucket"), ("wt_percentile", "ha_15m"), ("stochrsi_bucket", "ha_3m"), ("rsi_bucket", "adx_bucket"), ("time_bucket", "day_of_week"), ("wt_momentum", "rsi_bucket"), ("wt_percentile", "stochrsi_bucket")]
    for trade in completed_trades:
        conds = trade.get("conditions", {})
        for c1, c2 in important_combos:
            if c1 in conds and c2 in conds:
                key = f"{c1}={conds[c1]} + {c2}={conds[c2]}"
                combo_stats[key]["trade_count"] += 1
                combo_stats[key]["total_pnl"] += trade.get("pnl_pct", 0)
                if trade["is_win"]:
                    combo_stats[key]["wins"] += 1
                else:
                    combo_stats[key]["losses"] += 1
    for key, s in combo_stats.items():
        total = s["trade_count"]
        s["win_rate"] = (s["wins"] / total * 100) if total > 0 else 0
        s["avg_pnl"] = (s["total_pnl"] / total) if total > 0 else 0
    all_stats = {}
    for k, v in stats.items():
        all_stats[k] = {"win_rate": v["win_rate"], "avg_pnl": v["avg_pnl"], "trade_count": v["trade_count"], "wins": v["wins"], "losses": v["losses"]}
    for k, v in combo_stats.items():
        all_stats[k] = {"win_rate": v["win_rate"], "avg_pnl": v["avg_pnl"], "trade_count": v["trade_count"], "wins": v["wins"], "losses": v["losses"]}
    return all_stats


def find_sweet_spots_and_danger_zones(stats):
    significant = {k: v for k, v in stats.items() if v["trade_count"] >= MIN_TRADES_FOR_SIGNIFICANCE}
    sorted_by_wr = sorted(significant.items(), key=lambda x: (-x[1]["win_rate"], -x[1]["trade_count"]))
    sweet_spots = [(k, v) for k, v in sorted_by_wr if v["win_rate"] >= 90.0][:10]
    danger_zones = [(k, v) for k, v in reversed(sorted_by_wr) if v["win_rate"] < 80.0][:10]
    return sweet_spots, danger_zones


PARAM_MAP = {
    "wt_momentum=EXHAUST_DOWN": {"param": "WT_EXHAUST_DOWN_BONUS", "direction": "increase_on_high_wr", "default": 3.0},
    "wt_momentum=EXHAUST_UP": {"param": "WT_EXHAUST_UP_BONUS", "direction": "increase_on_high_wr", "default": 3.0},
    "wt_momentum=IMPULSE_DOWN": {"param": "WT_IMPULSE_DOWN_PENALTY", "direction": "decrease_on_low_wr", "default": -2.0},
    "wt_momentum=IMPULSE_UP": {"param": "WT_IMPULSE_UP_PENALTY", "direction": "decrease_on_low_wr", "default": -2.0},
    "adx_bucket=<20_ranging": {"param": "ADX_RANGING_THRESHOLD", "direction": "increase_on_low_wr", "default": 20.0},
    "adx_bucket=>40_strong": {"param": "ADX_TRENDING_THRESHOLD", "direction": "decrease_on_high_wr", "default": 25.0},
    "wt_percentile=>90": {"param": "WT_EXTREME_OB_BLOCK", "direction": "increase_on_low_wr", "default": 0.0},
    "rsi_bucket=<30": {"param": "RSI_ENTRY_MAX_LONG", "direction": "adjust_rsi_long", "default": 37.0},
    "rsi_bucket=>70": {"param": "RSI_ENTRY_MIN_SHORT", "direction": "adjust_rsi_short", "default": 63.0},
    "stochrsi_bucket=<20": {"param": "K_ZONE_LONG_THRESHOLD", "direction": "adjust_kzone_long", "default": 90},
    "stochrsi_bucket=>80": {"param": "K_ZONE_SHORT_THRESHOLD", "direction": "adjust_kzone_short", "default": 10},
    "volratio_bucket=>2.0": {"param": "ENTRY_VOL_MIN_RATIO", "direction": "decrease_on_high_wr", "default": 1.3},
}


def generate_recommendations(stats, sweet_spots, danger_zones):
    recommendations = []
    for cond_key, cond_stats in stats.items():
        if cond_stats["trade_count"] < MIN_TRADES_FOR_SIGNIFICANCE:
            continue
        if cond_key not in PARAM_MAP:
            continue
        mapping = PARAM_MAP[cond_key]
        param_name = mapping["param"]
        if param_name in PROTECTED_PARAMS:
            continue
        current_val = getattr(CFG, param_name, mapping["default"])
        wr = cond_stats["win_rate"]
        direction = mapping["direction"]
        recommended = current_val
        confidence = "LOW"
        reason_text = ""
        if "increase_on_high_wr" in direction and wr >= 90:
            bump = min(1.5, abs(current_val) * 0.5) if current_val != 0 else 1.5
            recommended = min(current_val + bump, current_val + SAFETY_SCORE_BOUND)
            confidence = "HIGH" if wr >= 95 else "MEDIUM"
            reason_text = f"{wr:.1f}% WR on {cond_stats['trade_count']} trades"
        elif "decrease_on_low_wr" in direction and wr < 75:
            bump = min(1.5, abs(current_val) * 0.3) if current_val != 0 else -1.5
            recommended = max(current_val - bump, current_val - SAFETY_SCORE_BOUND)
            confidence = "HIGH" if wr < 60 else "MEDIUM"
            reason_text = f"Only {wr:.1f}% WR on {cond_stats['trade_count']} trades"
        elif "increase_on_low_wr" in direction and wr < 75:
            bump = abs(current_val) * 0.1
            recommended = current_val + bump
            max_allowed = current_val * (1 + SAFETY_THRESHOLD_PCT)
            recommended = min(recommended, max_allowed)
            confidence = "MEDIUM" if wr < 65 else "LOW"
            reason_text = f"{wr:.1f}% WR when {cond_key} — tighten threshold"
        elif "decrease_on_high_wr" in direction and wr >= 90:
            bump = abs(current_val) * 0.1
            recommended = current_val - bump
            min_allowed = current_val * (1 - SAFETY_THRESHOLD_PCT)
            recommended = max(recommended, min_allowed)
            confidence = "MEDIUM"
            reason_text = f"{wr:.1f}% WR — loosen threshold to capture more"
        else:
            continue
        if abs(recommended - current_val) < 0.001:
            continue
        if isinstance(current_val, int):
            recommended = int(round(recommended))
        else:
            recommended = round(recommended, 4)
        recommendations.append({"param": param_name, "current": current_val, "recommended": recommended, "reason": reason_text, "confidence": confidence, "condition": cond_key, "win_rate": wr, "trade_count": cond_stats["trade_count"]})
    recommendations.sort(key=lambda r: (0 if r["confidence"] == "HIGH" else 1 if r["confidence"] == "MEDIUM" else 2, -r["trade_count"]))
    return recommendations


def run_analysis(days_back=1):
    logger.info(f"Running analysis over last {days_back} day(s)...")
    trades = load_decisions(days_back=days_back)
    logger.info(f"Loaded {len(trades)} raw trade records")
    completed = match_opens_and_closes(trades)
    logger.info(f"Matched {len(completed)} completed trades (open→close pairs)")
    if not completed:
        logger.warning("No completed trades found — cannot analyze")
        return None
    crypto_trades = [t for t in completed if t.get("is_crypto", True)]
    stock_trades = [t for t in completed if not t.get("is_crypto", True)]
    crypto_wr = (sum(1 for t in crypto_trades if t["is_win"]) / len(crypto_trades) * 100) if crypto_trades else 0
    stock_wr = (sum(1 for t in stock_trades if t["is_win"]) / len(stock_trades) * 100) if stock_trades else 0
    crypto_avg_pnl = (sum(t["pnl_pct"] for t in crypto_trades) / len(crypto_trades)) if crypto_trades else 0
    stock_avg_pnl = (sum(t["pnl_pct"] for t in stock_trades) / len(stock_trades)) if stock_trades else 0
    overall_wr = sum(1 for t in completed if t["is_win"]) / len(completed) * 100
    stats = compute_condition_stats(completed)
    sweet_spots, danger_zones = find_sweet_spots_and_danger_zones(stats)
    recommendations = generate_recommendations(stats, sweet_spots, danger_zones)
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "analysis_period": f"{days_back}d", "trades_analyzed": len(completed), "crypto_trades": len(crypto_trades), "stock_trades": len(stock_trades), "current_wr": round(overall_wr, 2), "crypto_wr": round(crypto_wr, 2), "stock_wr": round(stock_wr, 2), "crypto_avg_pnl": round(crypto_avg_pnl, 4), "stock_avg_pnl": round(stock_avg_pnl, 4), "target_wr": 90.0, "sweet_spots": [{"condition": k, "win_rate": round(v["win_rate"], 1), "trade_count": v["trade_count"], "avg_pnl": round(v["avg_pnl"], 4)} for k, v in sweet_spots], "danger_zones": [{"condition": k, "win_rate": round(v["win_rate"], 1), "trade_count": v["trade_count"], "avg_pnl": round(v["avg_pnl"], 4)} for k, v in danger_zones], "recommendations": recommendations, "condition_stats": {k: {"win_rate": round(v["win_rate"], 1), "trade_count": v["trade_count"], "avg_pnl": round(v["avg_pnl"], 4)} for k, v in stats.items() if v["trade_count"] >= 5}}
    return result


def save_recommendations(result):
    if result is None:
        return
    with open(RECOMMENDED_FILE, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info(f"Saved recommendations to {RECOMMENDED_FILE}")
    snapshot_name = f"dashboard_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"
    snapshot_path = TUNER_DIR / snapshot_name
    with open(snapshot_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info(f"Saved dashboard snapshot to {snapshot_path}")
    for rec in result.get("recommendations", []):
        entry = {"timestamp": result["timestamp"], "param": rec["param"], "current": rec["current"], "recommended": rec["recommended"], "reason": rec["reason"], "confidence": rec["confidence"], "trades_analyzed": result["trades_analyzed"], "current_wr": result["current_wr"]}
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")


def print_dashboard(result=None):
    if result is None:
        if RECOMMENDED_FILE.exists():
            with open(RECOMMENDED_FILE, "r") as f:
                result = json.load(f)
        else:
            print("No analysis data found. Run --analyze first.")
            return
    ts = result.get("timestamp", "?")[:19]
    print(f"\n{'═' * 60}")
    print(f" TRADE AUTO-TUNER — {ts} UTC")
    print(f"{'═' * 60}")
    print(f"Crypto: {result.get('crypto_trades', 0)} trades ({result.get('analysis_period', '?')}) | WR: {result.get('crypto_wr', 0):.1f}% | Avg PnL: {result.get('crypto_avg_pnl', 0):+.4f}%")
    print(f"Stocks: {result.get('stock_trades', 0)} trades ({result.get('analysis_period', '?')}) | WR: {result.get('stock_wr', 0):.1f}% | Avg PnL: {result.get('stock_avg_pnl', 0):+.4f}%")
    print(f"Overall: {result.get('trades_analyzed', 0)} trades | WR: {result.get('current_wr', 0):.1f}% | Target: {result.get('target_wr', 90.0):.1f}%")
    sweet = result.get("sweet_spots", [])
    if sweet:
        print(f"\nSWEET SPOTS (>90% WR):")
        for s in sweet[:10]:
            print(f"  {s['condition']}: {s['win_rate']:.1f}% WR ({s['trade_count']} trades) | Avg PnL: {s['avg_pnl']:+.4f}%")
    danger = result.get("danger_zones", [])
    if danger:
        print(f"\nDANGER ZONES (<80% WR):")
        for d in danger[:10]:
            print(f"  {d['condition']}: {d['win_rate']:.1f}% WR ({d['trade_count']} trades) | Avg PnL: {d['avg_pnl']:+.4f}%")
    recs = result.get("recommendations", [])
    if recs:
        print(f"\nPARAMETER ADJUSTMENTS RECOMMENDED:")
        for r in recs:
            print(f"  [{r['confidence']}] {r['param']}: {r['current']} -> {r['recommended']} ({r['reason']})")
    if not sweet and not danger and not recs:
        print("\nNo significant patterns found yet. Need more trade data.")
    print(f"{'═' * 60}\n")


def show_history():
    if not HISTORY_FILE.exists():
        print("No adjustment history found.")
        return
    print(f"\n{'═' * 60}")
    print(f" ADJUSTMENT HISTORY")
    print(f"{'═' * 60}")
    entries = []
    with open(HISTORY_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not entries:
        print("No entries found.")
        return
    for e in entries[-50:]:
        ts = e.get("timestamp", "?")[:19]
        print(f"  [{ts}] [{e.get('confidence', '?')}] {e.get('param', '?')}: {e.get('current', '?')} -> {e.get('recommended', '?')} | {e.get('reason', '')} | Overall WR: {e.get('current_wr', '?')}%")
    print(f"\nTotal entries: {len(entries)}")
    print(f"{'═' * 60}\n")


def apply_recommendations():
    if not RECOMMENDED_FILE.exists():
        print("No recommendations found. Run --analyze first.")
        return
    with open(RECOMMENDED_FILE, "r") as f:
        result = json.load(f)
    recs = result.get("recommendations", [])
    if not recs:
        print("No recommendations to apply.")
        return
    high_recs = [r for r in recs if r["confidence"] == "HIGH"]
    medium_recs = [r for r in recs if r["confidence"] == "MEDIUM"]
    print(f"\n{'═' * 60}")
    print(f" APPLY RECOMMENDATIONS")
    print(f"{'═' * 60}")
    print(f"\nHIGH confidence ({len(high_recs)}):")
    for r in high_recs:
        print(f"  {r['param']}: {r['current']} -> {r['recommended']} ({r['reason']})")
    print(f"\nMEDIUM confidence ({len(medium_recs)}):")
    for r in medium_recs:
        print(f"  {r['param']}: {r['current']} -> {r['recommended']} ({r['reason']})")
    print(f"\nRecommendations saved to: {RECOMMENDED_FILE}")
    print("To apply, manually update config.py with the recommended values.")
    print(f"{'═' * 60}\n")


def compute_before_after():
    now = datetime.now(timezone.utc)
    all_trades = load_decisions(days_back=7)
    completed = match_opens_and_closes(all_trades)
    if not completed:
        print("No completed trades in last 7 days.")
        return
    midpoint = now - timedelta(days=3.5)
    before = [t for t in completed if _parse_timestamp(t.get("entry_time", "")) and _parse_timestamp(t["entry_time"]) < midpoint]
    after = [t for t in completed if _parse_timestamp(t.get("entry_time", "")) and _parse_timestamp(t["entry_time"]) >= midpoint]
    def _summarize(trades, label):
        if not trades:
            return {"label": label, "count": 0, "wr": 0, "avg_pnl": 0}
        wins = sum(1 for t in trades if t["is_win"])
        wr = wins / len(trades) * 100
        avg_pnl = sum(t["pnl_pct"] for t in trades) / len(trades)
        return {"label": label, "count": len(trades), "wr": round(wr, 2), "avg_pnl": round(avg_pnl, 4)}
    b = _summarize(before, "Before (days 1-3.5)")
    a = _summarize(after, "After (days 3.5-7)")
    print(f"\n{'═' * 60}")
    print(f" BEFORE/AFTER COMPARISON (7-day rolling window)")
    print(f"{'═' * 60}")
    print(f"\n  {'Metric':<25} {'Before':>12} {'After':>12} {'Change':>12}")
    print(f"  {'-'*61}")
    print(f"  {'Trade Count':<25} {b['count']:>12} {a['count']:>12} {a['count'] - b['count']:>+12}")
    wr_delta = a["wr"] - b["wr"]
    print(f"  {'Win Rate %':<25} {b['wr']:>11.1f}% {a['wr']:>11.1f}% {wr_delta:>+11.1f}%")
    pnl_delta = a["avg_pnl"] - b["avg_pnl"]
    print(f"  {'Avg PnL %':<25} {b['avg_pnl']:>+11.4f}% {a['avg_pnl']:>+11.4f}% {pnl_delta:>+11.4f}%")
    if wr_delta > 0:
        print(f"\n  Result: IMPROVING (+{wr_delta:.1f}% WR)")
    elif wr_delta < 0:
        print(f"\n  Result: DECLINING ({wr_delta:.1f}% WR)")
    else:
        print(f"\n  Result: STABLE")
    print(f"{'═' * 60}\n")


class AutoTunerDaemon:
    def __init__(self):
        self._running = True
        self._last_analysis = 0
        self._last_adjustment = 0
        self._last_monitor = 0
        self._trade_count_seen = 0

    def stop(self):
        self._running = False
        logger.info("Shutdown signal received")

    async def run(self):
        logger.info("Trade Auto-Tuner daemon starting...")
        loop = asyncio.get_event_loop()
        for sig_name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig_name, self.stop)
        logger.info(f"Monitoring accounts: crypto={CRYPTO_ACCOUNTS}, stocks={STOCK_ACCOUNTS}")
        logger.info(f"Decisions dir: {DECISIONS_DIR}")
        logger.info(f"Output dir: {TUNER_DIR}")
        while self._running:
            try:
                now = time.time()
                if now - self._last_monitor >= 60:
                    self._last_monitor = now
                    await self._monitor_cycle()
                if now - self._last_analysis >= 7200:
                    self._last_analysis = now
                    await self._analysis_cycle()
                if now - self._last_adjustment >= 21600:
                    self._last_adjustment = now
                    await self._adjustment_cycle()
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daemon loop error: {e}", exc_info=True)
                await asyncio.sleep(30)
        logger.info("Trade Auto-Tuner daemon stopped")

    async def _monitor_cycle(self):
        try:
            trades = load_decisions(days_back=1)
            new_count = len(trades)
            if new_count != self._trade_count_seen:
                delta = new_count - self._trade_count_seen
                logger.info(f"Trade monitor: {new_count} trades today ({delta:+d} since last check)")
                self._trade_count_seen = new_count
        except Exception as e:
            logger.error(f"Monitor cycle error: {e}", exc_info=True)

    async def _analysis_cycle(self):
        try:
            logger.info("Starting 2-hour analysis cycle...")
            result = run_analysis(days_back=1)
            if result:
                save_recommendations(result)
                print_dashboard(result)
                logger.info(f"Analysis complete: {result['trades_analyzed']} trades, WR={result['current_wr']:.1f}%, {len(result.get('recommendations', []))} recommendations")
            else:
                logger.warning("Analysis returned no results")
        except Exception as e:
            logger.error(f"Analysis cycle error: {e}", exc_info=True)

    async def _adjustment_cycle(self):
        try:
            logger.info("Starting 6-hour adjustment cycle...")
            result = run_analysis(days_back=1)
            if result:
                save_recommendations(result)
                recs = result.get("recommendations", [])
                high_count = sum(1 for r in recs if r["confidence"] == "HIGH")
                logger.info(f"Adjustment cycle: {len(recs)} recommendations ({high_count} HIGH confidence)")
                if recs:
                    logger.info("Recommendations written to recommended_config.json (manual approval required)")
        except Exception as e:
            logger.error(f"Adjustment cycle error: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="Trade Auto-Tuner: monitor trades, analyze conditions, recommend config adjustments")
    parser.add_argument("--analyze", action="store_true", help="Run one analysis cycle and exit")
    parser.add_argument("--dashboard", action="store_true", help="Show latest dashboard")
    parser.add_argument("--history", action="store_true", help="Show adjustment history")
    parser.add_argument("--apply", action="store_true", help="Show recommendations to apply")
    parser.add_argument("--compare", action="store_true", help="Show before/after comparison")
    parser.add_argument("--days", type=int, default=1, help="Days of history to analyze (default: 1)")
    args = parser.parse_args()
    if args.analyze:
        result = run_analysis(days_back=args.days)
        if result:
            save_recommendations(result)
            print_dashboard(result)
        else:
            print("No completed trades found.")
        return
    if args.dashboard:
        print_dashboard()
        return
    if args.history:
        show_history()
        return
    if args.apply:
        apply_recommendations()
        return
    if args.compare:
        compute_before_after()
        return
    daemon = AutoTunerDaemon()
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
