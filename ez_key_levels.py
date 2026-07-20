"""
ez_key_levels.py — Key Level Detection Engine
==============================================
Three-pillar level system:
1. DC Breakout/Breakdown detection across timeframes (dc_low/dc_high breaks)
2. Diagonal trendlines connecting swing lows (longs) / swing highs (shorts)
3. Fibonacci retracement/extension levels on D/W/M charts

Publishes levels to Redis and disk for consumption by ez_positions_quick.py
"""

import json
import logging
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup — NEVER hardcode paths
# ---------------------------------------------------------------------------
if platform.system() == "Darwin":
    _BASE = Path("/Users/niels/Documents/binance")
else:
    _BASE = Path("/home/niels/binance")

ENV_BASE = os.environ.get("BASE_PATH")
if ENV_BASE:
    _BASE = Path(ENV_BASE)

KLINES_DIR = _BASE / "klines_cache"
DATA_DIR = _BASE / "data"
KEY_LEVELS_DIR = DATA_DIR / "key_levels"
KEY_LEVELS_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR = Path("/Users/niels/logs") if platform.system() == "Darwin" else Path("/home/niels/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "ez_key_levels.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("key_levels")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Timeframes to analyze (ordered low→high)
TIMEFRAMES = ["3m", "15m", "1h", "4h", "D", "W", "M"]

# DC windows per timeframe
DC_WINDOWS = {
    "3m": 20, "15m": 20, "1h": 20, "4h": 20,
    "D": 20, "W": 20, "M": 20,
}

# Fibonacci ratios
FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618, 2.0, 2.618]
FIB_LABELS = ["0%", "23.6%", "38.2%", "50%", "61.8%", "78.6%", "100%", "127.2%", "161.8%", "200%", "261.8%"]

# Swing detection: minimum bars between pivots
SWING_LOOKBACK = {"3m": 5, "15m": 5, "1h": 5, "4h": 5, "D": 5, "W": 3, "M": 3}

# How many swings to use for trendline fitting
TRENDLINE_MIN_POINTS = 3
TRENDLINE_MAX_POINTS = 8

# Fibonacci lookback (how many bars to find swing high/low)
FIB_LOOKBACK = {"D": 120, "W": 52, "M": 24}

# Level strength thresholds
STRONG_LEVEL_TOUCHES = 3  # touched 3+ times = strong


# ═══════════════════════════════════════════════════════════════════════════
# 1. KLINE LOADING
# ═══════════════════════════════════════════════════════════════════════════

def load_klines(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    """Load kline data from cache, return as DataFrame."""
    fpath = KLINES_DIR / f"{symbol}_{tf}.json"
    if not fpath.exists():
        return None
    try:
        with open(fpath, "r") as f:
            data = json.load(f)
        if not data:
            return None
        df = pd.DataFrame(data)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.sort_values("timestamp").reset_index(drop=True)
        return df
    except Exception as e:
        log.error(f"Failed loading klines {symbol}/{tf}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 2. DONCHIAN CHANNEL BREAKOUT/BREAKDOWN DETECTION
# ═══════════════════════════════════════════════════════════════════════════

def compute_dc_levels(df: pd.DataFrame, window: int = 20) -> Dict[str, Any]:
    """
    Compute Donchian Channel and detect breaks.
    Returns current dc_high, dc_low, whether price broke above/below,
    and the number of consecutive bars above/below.
    """
    if df is None or len(df) < window + 2:
        return {}

    high_roll = df["high"].rolling(window, min_periods=window).max()
    low_roll = df["low"].rolling(window, min_periods=window).min()

    # Previous bar's DC (for crossover detection)
    dc_high_prev = high_roll.iloc[-2] if len(high_roll) >= 2 else None
    dc_low_prev = low_roll.iloc[-2] if len(low_roll) >= 2 else None
    dc_high = high_roll.iloc[-1]
    dc_low = low_roll.iloc[-1]

    if pd.isna(dc_high) or pd.isna(dc_low):
        return {}

    price = df["close"].iloc[-1]
    price_prev = df["close"].iloc[-2] if len(df) >= 2 else price

    dc_basis = (dc_high + dc_low) / 2.0
    dc_width_pct = ((dc_high - dc_low) / dc_low * 100) if dc_low > 0 else 0

    # Crossover detection
    broke_high = False
    broke_low = False
    if dc_high_prev is not None and not pd.isna(dc_high_prev):
        broke_high = price_prev <= dc_high_prev and price > dc_high
        broke_low = price_prev >= dc_low_prev and price < dc_low

    # Count consecutive bars above dc_high or below dc_low
    bars_above = 0
    bars_below = 0
    for i in range(len(df) - 1, max(len(df) - 50, -1), -1):
        dc_h_i = high_roll.iloc[i] if i < len(high_roll) and not pd.isna(high_roll.iloc[i]) else None
        dc_l_i = low_roll.iloc[i] if i < len(low_roll) and not pd.isna(low_roll.iloc[i]) else None
        if dc_h_i is not None and df["close"].iloc[i] > dc_h_i:
            bars_above += 1
        else:
            break
    for i in range(len(df) - 1, max(len(df) - 50, -1), -1):
        dc_h_i = high_roll.iloc[i] if i < len(high_roll) and not pd.isna(high_roll.iloc[i]) else None
        dc_l_i = low_roll.iloc[i] if i < len(low_roll) and not pd.isna(low_roll.iloc[i]) else None
        if dc_l_i is not None and df["close"].iloc[i] < dc_l_i:
            bars_below += 1
        else:
            break

    # DC position (0=at low, 1=at high)
    dc_range = dc_high - dc_low
    dc_position = (price - dc_low) / dc_range if dc_range > 0 else 0.5

    return {
        "dc_high": float(dc_high),
        "dc_low": float(dc_low),
        "dc_basis": float(dc_basis),
        "dc_width_pct": float(dc_width_pct),
        "dc_position": float(dc_position),
        "broke_high": broke_high,
        "broke_low": broke_low,
        "bars_above_high": bars_above,
        "bars_below_low": bars_below,
        "price": float(price),
    }


def multi_tf_dc_analysis(symbol: str) -> Dict[str, Any]:
    """
    Analyze DC breaks across ALL timeframes.
    Returns a composite score: how many TFs confirm a breakout or breakdown.
    """
    result = {"symbol": symbol, "timeframes": {}, "breakout_score": 0, "breakdown_score": 0}

    for tf in TIMEFRAMES:
        df = load_klines(symbol, tf)
        if df is None:
            continue
        dc = compute_dc_levels(df, DC_WINDOWS.get(tf, 20))
        if not dc:
            continue
        result["timeframes"][tf] = dc

        # Score: higher TF breaks count more
        tf_weight = {"3m": 1, "15m": 2, "1h": 3, "4h": 5, "D": 8, "W": 13, "M": 21}
        w = tf_weight.get(tf, 1)

        if dc["broke_high"] or dc["bars_above_high"] > 0:
            result["breakout_score"] += w
        if dc["broke_low"] or dc["bars_below_low"] > 0:
            result["breakdown_score"] += w

    # Overall verdict
    if result["breakout_score"] > result["breakdown_score"] * 1.5:
        result["verdict"] = "BREAKOUT"
    elif result["breakdown_score"] > result["breakout_score"] * 1.5:
        result["verdict"] = "BREAKDOWN"
    else:
        result["verdict"] = "NEUTRAL"

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 3. DIAGONAL TRENDLINES (Swing-Connected)
# ═══════════════════════════════════════════════════════════════════════════

def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> Tuple[List[dict], List[dict]]:
    """
    Find swing highs and swing lows using a rolling window.
    A swing high = high[i] > all highs in [i-lookback, i+lookback]
    A swing low  = low[i]  < all lows  in [i-lookback, i+lookback]
    """
    swing_highs = []
    swing_lows = []

    if df is None or len(df) < lookback * 2 + 1:
        return swing_highs, swing_lows

    highs = df["high"].values
    lows = df["low"].values

    for i in range(lookback, len(df) - lookback):
        # Swing high
        window_highs = highs[i - lookback:i + lookback + 1]
        if highs[i] == np.max(window_highs):
            swing_highs.append({
                "index": i,
                "price": float(highs[i]),
                "timestamp": str(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i,
            })

        # Swing low
        window_lows = lows[i - lookback:i + lookback + 1]
        if lows[i] == np.min(window_lows):
            swing_lows.append({
                "index": i,
                "price": float(lows[i]),
                "timestamp": str(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i,
            })

    return swing_highs, swing_lows


def fit_trendline(points: List[dict], ascending: bool = True) -> Optional[Dict[str, Any]]:
    """
    Fit a diagonal trendline through swing points using linear regression.
    ascending=True for support lines (connecting lows), False for resistance (highs).

    Returns slope, intercept, projected next level, R-squared, and touch count.
    """
    if len(points) < TRENDLINE_MIN_POINTS:
        return None

    # Use most recent points
    pts = points[-TRENDLINE_MAX_POINTS:]

    x = np.array([p["index"] for p in pts], dtype=float)
    y = np.array([p["price"] for p in pts], dtype=float)

    # Filter: for ascending support, only keep higher lows
    if ascending:
        filtered_x, filtered_y = [x[0]], [y[0]]
        for i in range(1, len(y)):
            if y[i] >= filtered_y[-1]:  # higher low
                filtered_x.append(x[i])
                filtered_y.append(y[i])
        if len(filtered_x) < TRENDLINE_MIN_POINTS:
            # Fallback: use all points
            filtered_x, filtered_y = list(x), list(y)
    else:
        # For descending resistance, only keep lower highs
        filtered_x, filtered_y = [x[0]], [y[0]]
        for i in range(1, len(y)):
            if y[i] <= filtered_y[-1]:  # lower high
                filtered_x.append(x[i])
                filtered_y.append(y[i])
        if len(filtered_x) < TRENDLINE_MIN_POINTS:
            filtered_x, filtered_y = list(x), list(y)

    fx = np.array(filtered_x)
    fy = np.array(filtered_y)

    if len(fx) < 2:
        return None

    # Linear regression
    n = len(fx)
    sum_x = np.sum(fx)
    sum_y = np.sum(fy)
    sum_xy = np.sum(fx * fy)
    sum_x2 = np.sum(fx ** 2)

    denom = n * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-10:
        return None

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R-squared
    y_pred = slope * fx + intercept
    ss_res = np.sum((fy - y_pred) ** 2)
    ss_tot = np.sum((fy - np.mean(fy)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    # Project forward: where is the trendline NOW (at last index)
    last_idx = max(p["index"] for p in points)
    projected_price = slope * last_idx + intercept

    # Project next bar
    next_projected = slope * (last_idx + 1) + intercept

    # Count how many original swing points are "close" to the line (within 0.5%)
    touches = 0
    for p in points:
        line_price = slope * p["index"] + intercept
        if line_price > 0 and abs(p["price"] - line_price) / line_price < 0.005:
            touches += 1

    # Slope as percentage per bar
    slope_pct = (slope / projected_price * 100) if projected_price > 0 else 0

    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "slope_pct_per_bar": float(slope_pct),
        "projected_current": float(projected_price),
        "projected_next": float(next_projected),
        "r_squared": float(r_squared),
        "touches": touches,
        "num_points": len(fx),
        "direction": "ascending" if slope > 0 else "descending",
        "is_strong": touches >= STRONG_LEVEL_TOUCHES and r_squared > 0.7,
    }


def compute_trendlines(symbol: str, tf: str) -> Dict[str, Any]:
    """Compute support and resistance trendlines for a symbol/timeframe."""
    df = load_klines(symbol, tf)
    if df is None:
        return {}

    lb = SWING_LOOKBACK.get(tf, 5)
    swing_highs, swing_lows = find_swing_points(df, lb)

    result = {"symbol": symbol, "timeframe": tf}

    # Support trendline (connecting swing lows, ascending)
    support = fit_trendline(swing_lows, ascending=True)
    if support:
        result["support_trendline"] = support

    # Resistance trendline (connecting swing highs, descending=False means we connect highs)
    resistance = fit_trendline(swing_highs, ascending=False)
    if resistance:
        result["resistance_trendline"] = resistance

    # Current price distance from trendlines
    price = float(df["close"].iloc[-1])
    result["price"] = price

    if support:
        dist_support = (price - support["projected_current"]) / price * 100 if price > 0 else 0
        result["pct_from_support"] = float(dist_support)
        result["below_support"] = price < support["projected_current"]

    if resistance:
        dist_resistance = (resistance["projected_current"] - price) / price * 100 if price > 0 else 0
        result["pct_from_resistance"] = float(dist_resistance)
        result["above_resistance"] = price > resistance["projected_current"]

    # Swing count stats
    result["swing_highs_count"] = len(swing_highs)
    result["swing_lows_count"] = len(swing_lows)

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 4. FIBONACCI LEVELS
# ═══════════════════════════════════════════════════════════════════════════

def compute_fibonacci(df: pd.DataFrame, lookback: int = 120) -> Dict[str, Any]:
    """
    Compute Fibonacci retracement and extension levels.
    Uses the highest high and lowest low in the lookback period.
    Determines trend direction to set correct fib orientation.
    """
    if df is None or len(df) < 10:
        return {}

    window = df.tail(lookback)
    swing_high = float(window["high"].max())
    swing_low = float(window["low"].min())

    if swing_high <= swing_low or swing_low <= 0:
        return {}

    high_idx = window["high"].idxmax()
    low_idx = window["low"].idxmin()
    price = float(df["close"].iloc[-1])

    # Determine if uptrend (low before high) or downtrend (high before low)
    uptrend = low_idx < high_idx
    fib_range = swing_high - swing_low

    levels = {}
    for ratio, label in zip(FIB_RATIOS, FIB_LABELS):
        if uptrend:
            # Uptrend fib: retracement from high
            level = swing_high - fib_range * ratio
        else:
            # Downtrend fib: retracement from low
            level = swing_low + fib_range * ratio
        levels[label] = float(level)

    # Find nearest support and resistance fib levels
    nearest_support = None
    nearest_resistance = None
    nearest_support_label = None
    nearest_resistance_label = None

    sorted_levels = sorted(levels.items(), key=lambda x: x[1])
    for label, lvl in sorted_levels:
        if lvl < price:
            nearest_support = lvl
            nearest_support_label = label
        elif lvl > price and nearest_resistance is None:
            nearest_resistance = lvl
            nearest_resistance_label = label

    # Fib zone (which zone is price in?)
    fib_position = (price - swing_low) / fib_range if fib_range > 0 else 0.5

    result = {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "uptrend": uptrend,
        "fib_range": float(fib_range),
        "fib_position": float(fib_position),
        "price": price,
        "levels": levels,
        "nearest_support": nearest_support,
        "nearest_support_label": nearest_support_label,
        "nearest_resistance": nearest_resistance,
        "nearest_resistance_label": nearest_resistance_label,
    }

    # Distance to key levels as percentages
    if nearest_support and price > 0:
        result["pct_above_support"] = (price - nearest_support) / price * 100
    if nearest_resistance and price > 0:
        result["pct_below_resistance"] = (nearest_resistance - price) / price * 100

    # Is price at a key fib level? (within 0.3%)
    for label, lvl in levels.items():
        if lvl > 0 and abs(price - lvl) / lvl < 0.003:
            result["at_fib_level"] = label
            break

    return result


def compute_multi_tf_fibonacci(symbol: str) -> Dict[str, Any]:
    """Compute Fibonacci levels on D, W, M timeframes."""
    result = {"symbol": symbol, "fibs": {}}

    for tf in ["D", "W", "M"]:
        df = load_klines(symbol, tf)
        if df is None:
            continue
        lookback = FIB_LOOKBACK.get(tf, 120)
        fib = compute_fibonacci(df, lookback)
        if fib:
            result["fibs"][tf] = fib

    # Confluence: find fib levels from different TFs that cluster together
    all_levels = []
    for tf, fib_data in result["fibs"].items():
        for label, price in fib_data.get("levels", {}).items():
            all_levels.append({"tf": tf, "label": label, "price": price})

    # Sort by price and find clusters (within 0.5% of each other)
    all_levels.sort(key=lambda x: x["price"])
    confluences = []
    i = 0
    while i < len(all_levels):
        cluster = [all_levels[i]]
        j = i + 1
        while j < len(all_levels):
            if all_levels[i]["price"] > 0:
                pct_diff = abs(all_levels[j]["price"] - all_levels[i]["price"]) / all_levels[i]["price"]
                if pct_diff < 0.005:  # 0.5% cluster
                    cluster.append(all_levels[j])
                    j += 1
                else:
                    break
            else:
                break
        if len(cluster) >= 2:
            avg_price = np.mean([c["price"] for c in cluster])
            confluences.append({
                "price": float(avg_price),
                "timeframes": [c["tf"] for c in cluster],
                "labels": [f"{c['tf']}:{c['label']}" for c in cluster],
                "strength": len(cluster),  # more TFs = stronger
            })
        i = j if j > i + 1 else i + 1

    result["confluences"] = confluences
    return result


# ═══════════════════════════════════════════════════════════════════════════
# 5. COMPOSITE KEY LEVEL SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

def compute_all_key_levels(symbol: str) -> Dict[str, Any]:
    """
    Master function: compute all three pillars for a symbol.
    Returns a unified key levels dictionary with actionable signals.
    """
    t0 = time.time()

    result = {
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Multi-TF Donchian Channel analysis
    dc = multi_tf_dc_analysis(symbol)
    result["dc_analysis"] = dc

    # 2. Trendlines on key timeframes
    trendlines = {}
    for tf in ["1h", "4h", "D", "W"]:
        tl = compute_trendlines(symbol, tf)
        if tl:
            trendlines[tf] = tl
    result["trendlines"] = trendlines

    # 3. Fibonacci levels
    fibs = compute_multi_tf_fibonacci(symbol)
    result["fibonacci"] = fibs

    # 4. COMPOSITE SIGNALS
    price = None
    for tf in ["3m", "15m", "1h", "4h", "D"]:
        if tf in dc.get("timeframes", {}):
            price = dc["timeframes"][tf].get("price")
            if price:
                break

    if price and price > 0:
        result["price"] = price

        # --- KEY SUPPORT LEVELS (sorted by distance from price) ---
        supports = []
        resistances = []

        # DC lows as support
        for tf, dc_data in dc.get("timeframes", {}).items():
            if "dc_low" in dc_data:
                dist = (price - dc_data["dc_low"]) / price * 100
                supports.append({
                    "price": dc_data["dc_low"],
                    "type": "dc_low",
                    "timeframe": tf,
                    "distance_pct": dist,
                    "broken": dc_data.get("bars_below_low", 0) > 0,
                })
            if "dc_high" in dc_data:
                dist = (dc_data["dc_high"] - price) / price * 100
                resistances.append({
                    "price": dc_data["dc_high"],
                    "type": "dc_high",
                    "timeframe": tf,
                    "distance_pct": dist,
                    "broken": dc_data.get("bars_above_high", 0) > 0,
                })

        # Trendline supports/resistances
        for tf, tl in trendlines.items():
            if "support_trendline" in tl:
                sl = tl["support_trendline"]
                dist = (price - sl["projected_current"]) / price * 100
                supports.append({
                    "price": sl["projected_current"],
                    "type": "trendline_support",
                    "timeframe": tf,
                    "distance_pct": dist,
                    "broken": tl.get("below_support", False),
                    "slope_pct": sl.get("slope_pct_per_bar", 0),
                    "r_squared": sl.get("r_squared", 0),
                    "is_strong": sl.get("is_strong", False),
                })
            if "resistance_trendline" in tl:
                rl = tl["resistance_trendline"]
                dist = (rl["projected_current"] - price) / price * 100
                resistances.append({
                    "price": rl["projected_current"],
                    "type": "trendline_resistance",
                    "timeframe": tf,
                    "distance_pct": dist,
                    "broken": tl.get("above_resistance", False),
                    "slope_pct": rl.get("slope_pct_per_bar", 0),
                    "r_squared": rl.get("r_squared", 0),
                    "is_strong": rl.get("is_strong", False),
                })

        # Fibonacci supports/resistances
        for tf, fib_data in fibs.get("fibs", {}).items():
            for label, lvl in fib_data.get("levels", {}).items():
                if lvl < price:
                    dist = (price - lvl) / price * 100
                    supports.append({
                        "price": lvl,
                        "type": f"fib_{label}",
                        "timeframe": tf,
                        "distance_pct": dist,
                    })
                elif lvl > price:
                    dist = (lvl - price) / price * 100
                    resistances.append({
                        "price": lvl,
                        "type": f"fib_{label}",
                        "timeframe": tf,
                        "distance_pct": dist,
                    })

        # Sort by distance (nearest first)
        supports.sort(key=lambda x: abs(x["distance_pct"]))
        resistances.sort(key=lambda x: abs(x["distance_pct"]))

        result["nearest_supports"] = supports[:10]
        result["nearest_resistances"] = resistances[:10]

        # --- CRASH DETECTION ---
        # Multiple TF dc_low breaks = CRASH
        dc_lows_broken = []
        for tf in ["15m", "1h", "4h", "D"]:
            if tf in dc.get("timeframes", {}):
                tf_data = dc["timeframes"][tf]
                if tf_data.get("broke_low") or tf_data.get("bars_below_low", 0) > 0:
                    dc_lows_broken.append(tf)

        result["dc_lows_broken"] = dc_lows_broken
        result["crash_signal"] = len(dc_lows_broken) >= 2  # 2+ TFs breaking down
        result["crash_severity"] = len(dc_lows_broken)

        # --- BREAKOUT DETECTION ---
        dc_highs_broken = []
        for tf in ["15m", "1h", "4h", "D"]:
            if tf in dc.get("timeframes", {}):
                tf_data = dc["timeframes"][tf]
                if tf_data.get("broke_high") or tf_data.get("bars_above_high", 0) > 0:
                    dc_highs_broken.append(tf)

        result["dc_highs_broken"] = dc_highs_broken
        result["breakout_signal"] = len(dc_highs_broken) >= 2
        result["breakout_strength"] = len(dc_highs_broken)

        # --- AT KEY LEVEL? ---
        at_key_level = False
        key_level_type = None
        for s in supports[:3]:
            if abs(s["distance_pct"]) < 0.3:
                at_key_level = True
                key_level_type = f"support_{s['type']}_{s['timeframe']}"
                break
        for r in resistances[:3]:
            if abs(r["distance_pct"]) < 0.3:
                at_key_level = True
                key_level_type = f"resistance_{r['type']}_{r['timeframe']}"
                break

        result["at_key_level"] = at_key_level
        result["key_level_type"] = key_level_type

        # --- ACTIONABLE VERDICT ---
        if result["crash_signal"] and result["crash_severity"] >= 3:
            result["action"] = "EMERGENCY_REDUCE_LONGS"
            result["urgency"] = "CRITICAL"
        elif result["crash_signal"]:
            result["action"] = "REDUCE_LONGS"
            result["urgency"] = "HIGH"
        elif result["breakout_signal"] and result["breakout_strength"] >= 3:
            result["action"] = "ADD_LONGS"
            result["urgency"] = "HIGH"
        elif result["breakout_signal"]:
            result["action"] = "FAVOR_LONGS"
            result["urgency"] = "MEDIUM"
        elif at_key_level:
            result["action"] = "WATCH"
            result["urgency"] = "LOW"
        else:
            result["action"] = "HOLD"
            result["urgency"] = "NONE"

    elapsed = time.time() - t0
    result["compute_time_ms"] = round(elapsed * 1000, 1)

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 6. BATCH PROCESSING + OUTPUT
# ═══════════════════════════════════════════════════════════════════════════

def load_symbols() -> List[str]:
    """Load active symbols from symbols.json."""
    sym_file = _BASE / "symbols.json"
    if not sym_file.exists():
        log.warning(f"symbols.json not found at {sym_file}")
        return []
    try:
        with open(sym_file) as f:
            data = json.load(f)
        # symbols.json can be a dict of account→symbols or a list
        if isinstance(data, dict):
            all_syms = set()
            for acct, syms in data.items():
                if isinstance(syms, list):
                    all_syms.update(syms)
                elif isinstance(syms, dict):
                    all_syms.update(syms.keys())
            return sorted(all_syms)
        elif isinstance(data, list):
            return sorted(data)
    except Exception as e:
        log.error(f"Failed loading symbols: {e}")
    return []


def save_key_levels(levels: Dict[str, Any], symbol: str):
    """Save key levels to disk."""
    fpath = KEY_LEVELS_DIR / f"{symbol}.json"
    try:
        with open(fpath, "w") as f:
            json.dump(levels, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Failed saving key levels for {symbol}: {e}")


def load_key_levels(symbol: str) -> Optional[Dict[str, Any]]:
    """Load key levels from disk."""
    fpath = KEY_LEVELS_DIR / f"{symbol}.json"
    if not fpath.exists():
        return None
    try:
        with open(fpath) as f:
            return json.load(f)
    except Exception:
        return None


def run_full_scan(symbols: Optional[List[str]] = None):
    """Run key level analysis on all symbols."""
    if symbols is None:
        symbols = load_symbols()

    log.info(f"Starting key level scan for {len(symbols)} symbols")
    t0 = time.time()

    alerts = []
    for i, sym in enumerate(symbols):
        try:
            levels = compute_all_key_levels(sym)
            save_key_levels(levels, sym)

            action = levels.get("action", "HOLD")
            urgency = levels.get("urgency", "NONE")

            if urgency in ("CRITICAL", "HIGH"):
                alerts.append({
                    "symbol": sym,
                    "action": action,
                    "urgency": urgency,
                    "crash_severity": levels.get("crash_severity", 0),
                    "breakout_strength": levels.get("breakout_strength", 0),
                    "dc_lows_broken": levels.get("dc_lows_broken", []),
                    "dc_highs_broken": levels.get("dc_highs_broken", []),
                })

            if (i + 1) % 20 == 0:
                log.info(f"  Scanned {i+1}/{len(symbols)}")

        except Exception as e:
            log.error(f"Failed scanning {sym}: {e}")

    elapsed = time.time() - t0
    log.info(f"Key level scan complete: {len(symbols)} symbols in {elapsed:.1f}s")

    if alerts:
        log.warning(f"=== {len(alerts)} ALERTS ===")
        for a in alerts:
            log.warning(f"  {a['symbol']}: {a['action']} ({a['urgency']}) "
                       f"DC_lows_broken={a['dc_lows_broken']} DC_highs_broken={a['dc_highs_broken']}")

    # Save summary
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols_scanned": len(symbols),
        "alerts": alerts,
        "elapsed_s": round(elapsed, 1),
    }
    with open(KEY_LEVELS_DIR / "SCAN_SUMMARY.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    return summary


# ═══════════════════════════════════════════════════════════════════════════
# 7. CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Key Level Detection Engine")
    parser.add_argument("--symbol", type=str, help="Single symbol to analyze")
    parser.add_argument("--scan", action="store_true", help="Full scan all symbols")
    parser.add_argument("--report", action="store_true", help="Print report for symbol")
    args = parser.parse_args()

    if args.symbol:
        levels = compute_all_key_levels(args.symbol)
        save_key_levels(levels, args.symbol)

        if args.report:
            print(f"\n{'='*60}")
            print(f"  KEY LEVELS: {args.symbol}")
            print(f"{'='*60}")
            print(f"  Price: {levels.get('price', '?')}")
            print(f"  Action: {levels.get('action', '?')} ({levels.get('urgency', '?')})")
            print(f"  DC Verdict: {levels.get('dc_analysis', {}).get('verdict', '?')}")
            print(f"  Breakout Score: {levels.get('dc_analysis', {}).get('breakout_score', 0)}")
            print(f"  Breakdown Score: {levels.get('dc_analysis', {}).get('breakdown_score', 0)}")

            if levels.get("crash_signal"):
                print(f"  *** CRASH SIGNAL *** Severity: {levels.get('crash_severity')}")
                print(f"  DC Lows Broken: {levels.get('dc_lows_broken', [])}")

            if levels.get("breakout_signal"):
                print(f"  *** BREAKOUT SIGNAL *** Strength: {levels.get('breakout_strength')}")
                print(f"  DC Highs Broken: {levels.get('dc_highs_broken', [])}")

            print(f"\n  Nearest Supports:")
            for s in levels.get("nearest_supports", [])[:5]:
                broken = " [BROKEN]" if s.get("broken") else ""
                strong = " [STRONG]" if s.get("is_strong") else ""
                print(f"    {s['price']:>12.4f}  {s['type']:20s} ({s['timeframe']})  {s['distance_pct']:+.2f}%{broken}{strong}")

            print(f"\n  Nearest Resistances:")
            for r in levels.get("nearest_resistances", [])[:5]:
                broken = " [BROKEN]" if r.get("broken") else ""
                strong = " [STRONG]" if r.get("is_strong") else ""
                print(f"    {r['price']:>12.4f}  {r['type']:20s} ({r['timeframe']})  {r['distance_pct']:+.2f}%{broken}{strong}")

            # Fibonacci confluences
            confs = levels.get("fibonacci", {}).get("confluences", [])
            if confs:
                print(f"\n  Fib Confluences:")
                for c in confs:
                    print(f"    {c['price']:>12.4f}  Strength={c['strength']}  {', '.join(c['labels'])}")

            print(f"\n  Compute time: {levels.get('compute_time_ms', '?')}ms")
            print(f"{'='*60}")
        else:
            print(json.dumps(levels, indent=2, default=str))

    elif args.scan:
        run_full_scan()
    else:
        parser.print_help()
