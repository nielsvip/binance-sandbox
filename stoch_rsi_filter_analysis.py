"""
Stochastic Crossover + Higher-TF Filter Analysis
=================================================
Analyzes whether filtering stochastic crossover entries by higher-timeframe
directional bias improves trade outcomes.

Since RSI is not available in crypto decision JSONL, we use:
- k_15m > 50 (bullish bias) for longs, k_15m < 50 (bearish bias) for shorts
- wt1_15m > 0 (bullish) for longs, wt1_15m < 0 (bearish) for shorts
- k_3m > 50 / < 50 as a mid-TF filter

Crossover detection:
- LONG signal: k crosses above d (k > d at entry, stoch was recently below)
- SHORT signal: k crosses below d (k < d at entry, stoch was recently below)
We approximate crossover as k_Xm being within a threshold of d_Xm (recent cross).
"""

import json
import glob
import os
from collections import defaultdict
from datetime import datetime, timedelta

DATA_DIR = "/Users/niels/Documents/binance/data/decisions"
HISTORY_DIR = "/Users/niels/Documents/binance/data/history"
ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
LOOKBACK_DAYS = 14  # use 14 days for more data


def load_decisions(accounts, lookback_days):
    """Load all decision records from recent files."""
    cutoff = datetime.now() - timedelta(days=lookback_days)
    cutoff_str = cutoff.strftime("%Y%m%d")
    records = []
    for acct in accounts:
        pattern = os.path.join(DATA_DIR, f"decisions_{acct}_*.jsonl")
        files = sorted(glob.glob(pattern))
        for f in files:
            date_part = f.split("_")[-1].replace(".jsonl", "")
            if date_part < cutoff_str:
                continue
            with open(f, errors="ignore") as fh:
                for line in fh:
                    try:
                        d = json.loads(line.strip())
                        if not isinstance(d, dict):
                            continue
                        d["_file_date"] = date_part
                        records.append(d)
                    except (json.JSONDecodeError, ValueError):
                        continue
    return records


def parse_side(position_key):
    """Extract side from position_key like 'ang:BTCUSDT_LONG'."""
    if position_key.endswith("_LONG"):
        return "LONG"
    elif position_key.endswith("_SHORT"):
        return "SHORT"
    return None


def match_trades(records):
    """Match OPEN records with subsequent CLOSE/REDUCE on same position_key."""
    # Group by position_key
    by_key = defaultdict(list)
    for r in records:
        pk = r.get("position_key", "")
        by_key[pk].append(r)
    trades = []
    for pk, events in by_key.items():
        side = parse_side(pk)
        if not side:
            continue
        events.sort(key=lambda x: x.get("timestamp", ""))
        # Find OPEN -> next CLOSE/REDUCE pairs
        i = 0
        while i < len(events):
            ev = events[i]
            open_actions = ("OPEN", "QUICK_OPEN")
            close_actions = ("CLOSE", "REDUCE", "QUICK_CLOSE", "QUICK_REDUCE", "QUICK_QUICK_CLOSE", "NOW_REDUCE", "STRONG_REDUCE", "WEAK_REDUCE", "SCALP_REDUCE", "SCALP_PROFIT", "QUICK_EM_REDUCE_HARD_EXIT k150.0/50.0")
            if ev.get("action") in open_actions:
                snap = ev.get("snapshot", {})
                entry_price = snap.get("price") or 0
                if entry_price <= 0:
                    i += 1
                    continue
                # Find next CLOSE or REDUCE
                j = i + 1
                while j < len(events):
                    nev = events[j]
                    if nev.get("action") in close_actions:
                        nsnap = nev.get("snapshot", {})
                        exit_price = nsnap.get("price") or 0
                        if exit_price > 0:
                            if side == "LONG":
                                pnl_pct = (exit_price - entry_price) / entry_price * 100
                            else:
                                pnl_pct = (entry_price - exit_price) / entry_price * 100
                            trades.append({
                                "position_key": pk,
                                "side": side,
                                "account": ev.get("account", ""),
                                "entry_price": entry_price,
                                "exit_price": exit_price,
                                "pnl_pct": pnl_pct,
                                "entry_snap": snap,
                                "exit_snap": nsnap,
                                "entry_reason": ev.get("reason", ""),
                                "exit_reason": nev.get("reason", ""),
                                "entry_ts": ev.get("timestamp", ""),
                                "exit_ts": nev.get("timestamp", ""),
                            })
                        break
                    j += 1
                i = j + 1 if j < len(events) else i + 1
            else:
                i += 1
    return trades


def sget(snap, key, default=50):
    """Safely get numeric value from snapshot, returning default if None."""
    v = snap.get(key)
    return v if v is not None else default


def detect_crossover(snap, timeframe):
    """
    Detect if a stochastic crossover happened at entry.
    k > d = bullish crossover zone (for longs)
    k < d = bearish crossover zone (for shorts)
    'fresh' = k and d are within 10 points (recent cross)
    """
    k = snap.get(f"k_{timeframe}")
    d = snap.get(f"d_{timeframe}")
    if k is None or d is None:
        return None, None, None
    diff = k - d
    is_bullish = diff > 0  # k above d
    is_bearish = diff < 0  # k below d
    is_fresh = abs(diff) < 15  # recently crossed
    return is_bullish, is_bearish, is_fresh


def analyze_group(trades, label):
    """Compute stats for a group of trades."""
    if not trades:
        return {"label": label, "count": 0, "avg_pnl": 0, "win_rate": 0, "median_pnl": 0, "total_pnl": 0}
    pnls = [t["pnl_pct"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    pnls_sorted = sorted(pnls)
    median = pnls_sorted[len(pnls_sorted) // 2]
    return {
        "label": label,
        "count": len(trades),
        "avg_pnl": sum(pnls) / len(pnls),
        "median_pnl": median,
        "win_rate": wins / len(trades) * 100,
        "total_pnl": sum(pnls),
        "best": max(pnls),
        "worst": min(pnls),
    }


def print_table(results, title):
    """Print formatted results table."""
    print(f"\n{'='*100}")
    print(f"  {title}")
    print(f"{'='*100}")
    print(f"{'Group':<50} {'Count':>6} {'AvgPnL%':>9} {'MedPnL%':>9} {'WinRate%':>9} {'TotPnL%':>10} {'Best%':>8} {'Worst%':>8}")
    print(f"{'-'*100}")
    for r in results:
        if r["count"] == 0:
            print(f"{r['label']:<50} {'0':>6} {'N/A':>9} {'N/A':>9} {'N/A':>9} {'N/A':>10} {'N/A':>8} {'N/A':>8}")
        else:
            print(f"{r['label']:<50} {r['count']:>6} {r['avg_pnl']:>+9.4f} {r['median_pnl']:>+9.4f} {r['win_rate']:>8.1f}% {r['total_pnl']:>+10.3f} {r['best']:>+8.3f} {r['worst']:>+8.3f}")


def main():
    print("Loading decision data...")
    records = load_decisions(ACCOUNTS, LOOKBACK_DAYS)
    print(f"  Loaded {len(records)} decision records")
    action_counts = defaultdict(int)
    for r in records:
        action_counts[r.get("action", "?")] += 1
    print(f"  Actions: {dict(action_counts)}")
    print("\nMatching OPEN -> CLOSE/REDUCE trades...")
    trades = match_trades(records)
    print(f"  Matched {len(trades)} complete trades")
    if not trades:
        print("No trades to analyze!")
        return
    long_trades = [t for t in trades if t["side"] == "LONG"]
    short_trades = [t for t in trades if t["side"] == "SHORT"]
    print(f"  LONG trades: {len(long_trades)}, SHORT trades: {len(short_trades)}")
    # ========================================================================
    # ANALYSIS 1: 1m Stochastic Crossover with/without 15m Stoch Filter
    # ========================================================================
    for tf_cross in ["1m", "3m"]:
        for tf_filter, filter_field in [("3m", "k_3m"), ("15m", "k_15m"), ("15m_wt", "wt1_15m")]:
            results = []
            # --- LONGS ---
            # All longs with bullish crossover on tf_cross
            cross_longs = []
            cross_longs_filtered = []
            cross_longs_anti = []
            for t in long_trades:
                is_bull, is_bear, is_fresh = detect_crossover(t["entry_snap"], tf_cross)
                if is_bull and is_fresh:
                    cross_longs.append(t)
                    fval = t["entry_snap"].get(filter_field)
                    if fval is not None:
                        if filter_field.startswith("wt"):
                            if fval > 0:
                                cross_longs_filtered.append(t)
                            else:
                                cross_longs_anti.append(t)
                        else:
                            if fval > 50:
                                cross_longs_filtered.append(t)
                            else:
                                cross_longs_anti.append(t)
            results.append(analyze_group(cross_longs, f"LONG: {tf_cross} bullish cross (ALL)"))
            results.append(analyze_group(cross_longs_filtered, f"LONG: {tf_cross} bull cross + {tf_filter} BULLISH filter"))
            results.append(analyze_group(cross_longs_anti, f"LONG: {tf_cross} bull cross + {tf_filter} BEARISH (anti-filter)"))
            # --- SHORTS ---
            cross_shorts = []
            cross_shorts_filtered = []
            cross_shorts_anti = []
            for t in short_trades:
                is_bull, is_bear, is_fresh = detect_crossover(t["entry_snap"], tf_cross)
                if is_bear and is_fresh:
                    cross_shorts.append(t)
                    fval = t["entry_snap"].get(filter_field)
                    if fval is not None:
                        if filter_field.startswith("wt"):
                            if fval < 0:
                                cross_shorts_filtered.append(t)
                            else:
                                cross_shorts_anti.append(t)
                        else:
                            if fval < 50:
                                cross_shorts_filtered.append(t)
                            else:
                                cross_shorts_anti.append(t)
            results.append(analyze_group(cross_shorts, f"SHORT: {tf_cross} bearish cross (ALL)"))
            results.append(analyze_group(cross_shorts_filtered, f"SHORT: {tf_cross} bear cross + {tf_filter} BEARISH filter"))
            results.append(analyze_group(cross_shorts_anti, f"SHORT: {tf_cross} bear cross + {tf_filter} BULLISH (anti-filter)"))
            print_table(results, f"Stoch Cross on {tf_cross} | Filter: {tf_filter} ({filter_field})")
    # ========================================================================
    # ANALYSIS 2: Stochastic Zone Analysis (overbought/oversold)
    # ========================================================================
    results = []
    # Longs entered when 1m stoch is oversold (<20)
    oversold_longs = [t for t in long_trades if sget(t["entry_snap"], "k_1m", 50) < 20]
    neutral_longs = [t for t in long_trades if 20 <= sget(t["entry_snap"], "k_1m", 50) <= 80]
    overbought_longs = [t for t in long_trades if sget(t["entry_snap"], "k_1m", 50) > 80]
    results.append(analyze_group(oversold_longs, "LONG: 1m stoch OVERSOLD (<20) at entry"))
    results.append(analyze_group(neutral_longs, "LONG: 1m stoch NEUTRAL (20-80) at entry"))
    results.append(analyze_group(overbought_longs, "LONG: 1m stoch OVERBOUGHT (>80) at entry"))
    oversold_shorts = [t for t in short_trades if sget(t["entry_snap"], "k_1m", 50) > 80]
    neutral_shorts = [t for t in short_trades if 20 <= sget(t["entry_snap"], "k_1m", 50) <= 80]
    overbought_shorts = [t for t in short_trades if sget(t["entry_snap"], "k_1m", 50) < 20]
    results.append(analyze_group(oversold_shorts, "SHORT: 1m stoch OVERBOUGHT (>80) at entry [good]"))
    results.append(analyze_group(neutral_shorts, "SHORT: 1m stoch NEUTRAL (20-80) at entry"))
    results.append(analyze_group(overbought_shorts, "SHORT: 1m stoch OVERSOLD (<20) at entry [bad]"))
    print_table(results, "Stochastic Zone at Entry (1m)")
    # ========================================================================
    # ANALYSIS 3: Combined filter - crossover + zone + higher TF alignment
    # ========================================================================
    results = []
    # Best case: 3m bullish cross + 15m bullish + 1m oversold bounce
    best_longs = [t for t in long_trades
                  if detect_crossover(t["entry_snap"], "3m")[0]  # bullish
                  and detect_crossover(t["entry_snap"], "3m")[2]  # fresh
                  and sget(t["entry_snap"], "k_15m", 0) > 50  # 15m bullish
                  and sget(t["entry_snap"], "k_1m", 50) < 40]  # 1m not overbought
    worst_longs = [t for t in long_trades
                   if detect_crossover(t["entry_snap"], "3m")[0]  # bullish
                   and detect_crossover(t["entry_snap"], "3m")[2]  # fresh
                   and sget(t["entry_snap"], "k_15m", 0) < 50  # 15m bearish
                   and sget(t["entry_snap"], "k_1m", 50) > 60]  # 1m already high
    results.append(analyze_group(long_trades, "LONG: ALL trades (baseline)"))
    results.append(analyze_group(best_longs, "LONG: 3m bull cross + 15m>50 + 1m<40"))
    results.append(analyze_group(worst_longs, "LONG: 3m bull cross + 15m<50 + 1m>60 (BAD)"))
    best_shorts = [t for t in short_trades
                   if detect_crossover(t["entry_snap"], "3m")[1]  # bearish
                   and detect_crossover(t["entry_snap"], "3m")[2]  # fresh
                   and sget(t["entry_snap"], "k_15m", 0) < 50  # 15m bearish
                   and sget(t["entry_snap"], "k_1m", 50) > 60]  # 1m not oversold
    worst_shorts = [t for t in short_trades
                    if detect_crossover(t["entry_snap"], "3m")[1]  # bearish
                    and detect_crossover(t["entry_snap"], "3m")[2]  # fresh
                    and sget(t["entry_snap"], "k_15m", 0) > 50  # 15m bullish
                    and sget(t["entry_snap"], "k_1m", 50) < 40]  # 1m already low
    results.append(analyze_group(short_trades, "SHORT: ALL trades (baseline)"))
    results.append(analyze_group(best_shorts, "SHORT: 3m bear cross + 15m<50 + 1m>60"))
    results.append(analyze_group(worst_shorts, "SHORT: 3m bear cross + 15m>50 + 1m<40 (BAD)"))
    print_table(results, "Combined Multi-TF Filter (Best vs Worst Setups)")
    # ========================================================================
    # ANALYSIS 4: Heiken Ashi alignment
    # ========================================================================
    results = []
    ha_aligned_longs = [t for t in long_trades if t["entry_snap"].get("ha_3m") == "green" and t["entry_snap"].get("ha_15m") == "green"]
    ha_anti_longs = [t for t in long_trades if t["entry_snap"].get("ha_3m") == "red" and t["entry_snap"].get("ha_15m") == "red"]
    ha_mixed_longs = [t for t in long_trades if t not in ha_aligned_longs and t not in ha_anti_longs]
    results.append(analyze_group(ha_aligned_longs, "LONG: HA 3m+15m both GREEN (aligned)"))
    results.append(analyze_group(ha_mixed_longs, "LONG: HA mixed"))
    results.append(analyze_group(ha_anti_longs, "LONG: HA 3m+15m both RED (counter-trend)"))
    ha_aligned_shorts = [t for t in short_trades if t["entry_snap"].get("ha_3m") == "red" and t["entry_snap"].get("ha_15m") == "red"]
    ha_anti_shorts = [t for t in short_trades if t["entry_snap"].get("ha_3m") == "green" and t["entry_snap"].get("ha_15m") == "green"]
    ha_mixed_shorts = [t for t in short_trades if t not in ha_aligned_shorts and t not in ha_anti_shorts]
    results.append(analyze_group(ha_aligned_shorts, "SHORT: HA 3m+15m both RED (aligned)"))
    results.append(analyze_group(ha_mixed_shorts, "SHORT: HA mixed"))
    results.append(analyze_group(ha_anti_shorts, "SHORT: HA 3m+15m both GREEN (counter-trend)"))
    print_table(results, "Heiken Ashi Alignment at Entry")
    # ========================================================================
    # ANALYSIS 5: Sentiment alignment
    # ========================================================================
    results = []
    sent_bull_longs = [t for t in long_trades if (t["entry_snap"].get("sentiment") or 0) > 0]
    sent_bear_longs = [t for t in long_trades if (t["entry_snap"].get("sentiment") or 0) < 0]
    results.append(analyze_group(sent_bull_longs, "LONG: sentiment > 0 (bullish)"))
    results.append(analyze_group(sent_bear_longs, "LONG: sentiment < 0 (bearish) [contrarian]"))
    sent_bear_shorts = [t for t in short_trades if (t["entry_snap"].get("sentiment") or 0) < 0]
    sent_bull_shorts = [t for t in short_trades if (t["entry_snap"].get("sentiment") or 0) > 0]
    results.append(analyze_group(sent_bear_shorts, "SHORT: sentiment < 0 (bearish aligned)"))
    results.append(analyze_group(sent_bull_shorts, "SHORT: sentiment > 0 (bullish) [contrarian]"))
    print_table(results, "Sentiment Alignment at Entry")
    # Summary
    print(f"\n{'='*100}")
    print("  INTERPRETATION NOTES")
    print(f"{'='*100}")
    print("""
  - k_Xm / d_Xm = Stochastic %K / %D on X-minute timeframe
  - 'Fresh cross' = |k - d| < 15 (recently crossed)
  - Filter: k_15m > 50 = higher-TF bullish bias (proxy for RSI > 50)
  - Filter: wt1_15m > 0 = Wave Trend bullish (alternative to RSI)
  - PnL% = price change from OPEN to first CLOSE/REDUCE
  - Positive avg/median PnL = filter improves entries
  - Higher win rate WITH filter vs WITHOUT = filter is useful
  - 'Anti-filter' = entries that would be REJECTED by the filter
    (if anti-filter has worse PnL, the filter is adding value)
""")


if __name__ == "__main__":
    main()
