#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""Gate-by-Gate Analysis: replay profitable trader entries through our entry gates.

For each winning trade from the trader sweep, checks which specific gate
would have BLOCKED the entry and which gates pass.

Produces a clear breakdown: "Gate X kills Y% of profitable trades."
This tells us exactly what to loosen for more entries without guessing.

Usage:
    python3 gate_analysis.py                   # Full analysis
    python3 gate_analysis.py --symbol BTCUSDT  # Single symbol
    python3 gate_analysis.py --export          # Export CSV of all gate results
"""
import argparse
import csv
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = config.DATA_DIR
MERGED_DIR = DATA_DIR / "trader_sweep_merged"
RESULTS_DIR = DATA_DIR / "gate_analysis"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("gate_analysis")

NPZ_DIRS = [
    BASE_PATH / "backtest_v5" / "indicators_3m",  # Crypto 3m (best resolution)
    BASE_PATH / "backtest_v4" / "indicators",      # Crypto 15m
    BASE_PATH / "backtest_v8" / "indicators",      # Stocks (Tradier)
]


# ═══════════════════════════════════════════════════════════════════
# LOAD WINNING ENTRIES FROM TRADER SWEEP
# ═══════════════════════════════════════════════════════════════════

def load_winning_entries() -> List[Dict]:
    """Load winning entries from deep analyzer output or merged CSV."""
    # Try the unified merged CSV first
    merged = MERGED_DIR / "all_exchanges_merged.csv"
    if not merged.exists():
        merged = DATA_DIR / "bitget_traders" / "merged_all_trades.csv"
    if not merged.exists():
        logger.error("No merged trade data found")
        return []
    entries = []
    with open(merged) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                pnl = float(row.get("pnl", 0))
                if pnl <= 0:
                    continue
                entries.append(row)
            except (ValueError, TypeError):
                continue
    logger.info(f"Loaded {len(entries)} winning trades from {merged.name}")
    return entries


# ═══════════════════════════════════════════════════════════════════
# NPZ INDICATOR LOADER
# ═══════════════════════════════════════════════════════════════════

def load_npz(symbol: str) -> Optional[Dict]:
    """Load NPZ indicators for a symbol, searching all NPZ directories."""
    candidates = [symbol, symbol.replace("USDT", ""), symbol + "USDT", symbol.replace("-", "").replace("/", ""), symbol.replace("USDC", "USDT"), symbol.replace("-SWAP", "").replace("-", "") + "USDT"]
    for npz_dir in NPZ_DIRS:
        if not npz_dir.exists():
            continue
        for c in candidates:
            path = npz_dir / f"{c}.npz"
            if path.exists():
                data = np.load(str(path), allow_pickle=True)
                return {k: data[k] for k in data.files}
    return None


def get_indicators_at_time(npz: Dict, target_ts: int) -> Dict[str, float]:
    """Get all indicator values at a specific timestamp from NPZ."""
    timestamps = npz.get("timestamps", np.array([]))
    if len(timestamps) == 0:
        return {}
    idx = np.searchsorted(timestamps, target_ts, side="right") - 1
    if idx < 0 or idx >= len(timestamps):
        return {}
    result = {}
    for key, arr in npz.items():
        if key == "timestamps":
            continue
        if idx < len(arr):
            val = arr[idx]
            if isinstance(val, (np.floating, float)):
                if not np.isnan(val):
                    result[key] = float(val)
            elif isinstance(val, (np.integer, int)):
                result[key] = int(val)
            elif isinstance(val, (np.bytes_, bytes)):
                result[key] = val.decode("utf-8", errors="ignore")
            elif isinstance(val, np.ndarray):
                pass
            else:
                try:
                    result[key] = str(val)
                except Exception:
                    pass
    return result


# ═══════════════════════════════════════════════════════════════════
# GATE CHECKS — replicate our actual entry gates
# ═══════════════════════════════════════════════════════════════════

def _sf(val, default=0.0):
    """Safe float."""
    if val is None:
        return default
    try:
        v = float(val)
        return v if not np.isnan(v) else default
    except (ValueError, TypeError):
        return default


def check_gate_ltf_alignment(ind: Dict, is_long: bool) -> Tuple[bool, str]:
    """Gate 1a: LTF alignment (3m, 15m stoch K>D or K<D).
    Note: 1m not in NPZ. In live, requires 3/3 (1m+3m+15m).
    Here we check 2/2 (3m+15m) + report separately what 1m would need."""
    k_3m = _sf(ind.get("stoch_k_3m", ind.get("stoch_k")), 50)
    d_3m = _sf(ind.get("stoch_d_3m", ind.get("stoch_d")), 50)
    k_15m = _sf(ind.get("stoch_k_15m", ind.get("stoch_k")), 50)
    d_15m = _sf(ind.get("stoch_d_15m", ind.get("stoch_d")), 50)
    if is_long:
        ltf = int(k_3m > d_3m) + int(k_15m > d_15m)
    else:
        ltf = int(k_3m < d_3m) + int(k_15m < d_15m)
    if ltf < 2:
        return False, f"LTF_ALIGN({ltf}/2,k3m={k_3m:.0f},d3m={d_3m:.0f},k15m={k_15m:.0f})"
    return True, f"LTF_OK({ltf}/2)"


def check_gate_htf_alignment(ind: Dict, is_long: bool) -> Tuple[bool, str]:
    """Gate 1b: HTF 2/3 alignment (1h, 4h, D stoch K>D or K<D)."""
    k_1h = _sf(ind.get("stoch_k_1h"), 50)
    d_1h = _sf(ind.get("stoch_d_1h"), 50)
    k_4h = _sf(ind.get("stoch_k_4h"), 50)
    d_4h = _sf(ind.get("stoch_d_4h"), 50)
    k_D = _sf(ind.get("stoch_k_D", ind.get("stoch_k_1D")), 50)
    d_D = _sf(ind.get("stoch_d_D", ind.get("stoch_d_1D")), 50)
    ha_D = ind.get("ha_streak_D", ind.get("ha_streak_1D", ""))
    if is_long:
        htf = int(k_1h > d_1h) + int(k_4h > d_4h) + int(str(ha_D) == "green" or k_D > d_D)
    else:
        htf = int(k_1h < d_1h) + int(k_4h < d_4h) + int(str(ha_D) == "red" or k_D < d_D)
    if htf < 2:
        return False, f"HTF_ALIGN({htf}/3)"
    return True, "HTF_OK"


def check_gate_daily_mandatory(ind: Dict, is_long: bool) -> Tuple[bool, str]:
    """Gate 1c: Daily stoch or HA must align."""
    k_D = _sf(ind.get("stoch_k_D", ind.get("stoch_k_1D")), 50)
    d_D = _sf(ind.get("stoch_d_D", ind.get("stoch_d_1D")), 50)
    # ha_streak can be int (>0=green, <0=red) or string
    ha_raw = ind.get("ha_streak_D", ind.get("ha_streak_1D", 0))
    if isinstance(ha_raw, (int, float, np.integer, np.floating)):
        ha_green = float(ha_raw) > 0
        ha_red = float(ha_raw) < 0
    else:
        ha_green = str(ha_raw).lower() == "green"
        ha_red = str(ha_raw).lower() == "red"
    if is_long:
        aligned = ha_green or k_D > d_D
    else:
        aligned = ha_red or k_D < d_D
    if not aligned:
        return False, f"DAILY_MANDATORY(k={k_D:.0f},d={d_D:.0f},ha={ha_raw})"
    return True, "DAILY_OK"


def check_gate_k3m_exhaustion(ind: Dict, is_long: bool) -> Tuple[bool, str]:
    """Gate 1d/1e: k_3m >= 70 blocks LONG, k_3m <= 30 blocks SHORT."""
    k_3m = _sf(ind.get("stoch_k_3m", ind.get("stoch_k")), 50)
    if is_long and k_3m >= 70:
        return False, f"K3M_EXHAUSTED_LONG({k_3m:.0f}>=70)"
    if not is_long and k_3m <= 30:
        return False, f"K3M_EXHAUSTED_SHORT({k_3m:.0f}<=30)"
    return True, "K3M_OK"


def _is_bull_cross(val) -> bool:
    """Check if wt_cross value is bullish. Handles int8 (1=BULL) and string ('BULL')."""
    if isinstance(val, (int, float, np.integer, np.floating)):
        return int(val) == 1
    return str(val).upper() in ("BULL", "1", "TRUE")


def _is_bear_cross(val) -> bool:
    """Check if wt_cross value is bearish. Handles int8 (-1=BEAR) and string ('BEAR')."""
    if isinstance(val, (int, float, np.integer, np.floating)):
        return int(val) == -1
    return str(val).upper() in ("BEAR", "-1")


def check_gate_wt_cross(ind: Dict, is_long: bool) -> Tuple[bool, str]:
    """Gate 2: WT cross on 3m (1m not available in NPZ, so we check 3m only + bull/bear flags)."""
    # Note: 1m data doesn't exist in NPZ. In live, 1m is from real-time WebSocket.
    # For this analysis, we use wt_cross_3m + wt_cross_bull_3m/bear_3m as proxy.
    wt_cross_3m = ind.get("wt_cross_3m", 0)
    wt_cross_bull_3m = ind.get("wt_cross_bull_3m", 0)
    wt_cross_bear_3m = ind.get("wt_cross_bear_3m", 0)
    wt1_3m = _sf(ind.get("wt1_3m"), 0)
    wt2_3m = _sf(ind.get("wt2_3m"), 0)
    k_3m = _sf(ind.get("stoch_k_3m", ind.get("stoch_k")), 50)
    d_3m = _sf(ind.get("stoch_d_3m", ind.get("stoch_d")), 50)
    if is_long:
        co_3m = _is_bull_cross(wt_cross_3m) or _is_bull_cross(wt_cross_bull_3m) or (wt1_3m > wt2_3m and k_3m > d_3m)
        if co_3m:
            return True, "WT_CROSS_OK(3m_bull)"
        return False, f"WT_CROSS_NO_BULL_3M(wt_cross={wt_cross_3m},wt1={wt1_3m:.0f},wt2={wt2_3m:.0f})"
    else:
        cu_3m = _is_bear_cross(wt_cross_3m) or _is_bear_cross(wt_cross_bear_3m) or (wt1_3m < wt2_3m and k_3m < d_3m)
        if cu_3m:
            return True, "WT_CROSS_OK(3m_bear)"
        return False, f"WT_CROSS_NO_BEAR_3M(wt_cross={wt_cross_3m},wt1={wt1_3m:.0f},wt2={wt2_3m:.0f})"


def check_gate_dc_structure(ind: Dict, is_long: bool) -> Tuple[bool, str]:
    """Gate 3: DC breakout OR higher/lower structure on 15m."""
    dc_high_3m = _sf(ind.get("dc_high_3m", ind.get("dc_high")), 0)
    dc_high_3m_ant = _sf(ind.get("dc_high_3m_prev", ind.get("dc_high_prev")), 0)
    dc_low_3m = _sf(ind.get("dc_low_3m", ind.get("dc_low")), 0)
    dc_low_3m_ant = _sf(ind.get("dc_low_3m_prev", ind.get("dc_low_prev")), 0)
    low_15m = _sf(ind.get("low_15m", ind.get("low")), 0)
    low_15m_prev = _sf(ind.get("low_15m_prev", ind.get("low_prev")), 0)
    high_15m = _sf(ind.get("high_15m", ind.get("high")), 0)
    high_15m_prev = _sf(ind.get("high_15m_prev", ind.get("high_prev")), 0)
    if is_long:
        dc_breakout = dc_high_3m > 0 and dc_high_3m_ant > 0 and dc_high_3m > dc_high_3m_ant
        structure_ok = low_15m > 0 and low_15m_prev > 0 and low_15m > low_15m_prev
    else:
        dc_breakout = dc_low_3m > 0 and dc_low_3m_ant > 0 and dc_low_3m < dc_low_3m_ant
        structure_ok = high_15m > 0 and high_15m_prev > 0 and high_15m < high_15m_prev
    if dc_breakout or structure_ok:
        return True, f"STRUCT_OK(dc={dc_breakout},struct={structure_ok})"
    return False, "NO_STRUCT_OR_BREAKOUT"


def check_gate_adx_regime(ind: Dict) -> Tuple[bool, str]:
    """Gate: ADX < 20 = ranging market (optional gate)."""
    adx = _sf(ind.get("adx_14", ind.get("adx_1h")), 25)
    if adx < 20:
        return False, f"ADX_RANGING({adx:.0f}<20)"
    return True, f"ADX_OK({adx:.0f})"


def check_gate_mfi_direction(ind: Dict, is_long: bool) -> Tuple[bool, str]:
    """Gate: MFI alignment (not a hard block in crypto, but informational)."""
    mfi_1h = _sf(ind.get("mfi_1h", ind.get("mfi_14")), 50)
    mfi_4h = _sf(ind.get("mfi_4h"), 50)
    if is_long:
        score = int(mfi_1h > 50) + int(mfi_4h > 50)
    else:
        score = int(mfi_1h < 50) + int(mfi_4h < 50)
    if score == 0:
        return False, f"MFI_AGAINST(1h={mfi_1h:.0f},4h={mfi_4h:.0f})"
    return True, f"MFI_OK({score}/2)"


def check_gate_stoch_entry_zone(ind: Dict, is_long: bool) -> Tuple[bool, str]:
    """Gate: Stochastic entry zone (stock-style: k<35 for long, k>65 for short)."""
    k_5m = _sf(ind.get("stoch_k_5m", ind.get("stoch_k_3m", ind.get("stoch_k"))), 50)
    d_5m = _sf(ind.get("stoch_d_5m", ind.get("stoch_d_3m", ind.get("stoch_d"))), 50)
    if is_long:
        in_zone = k_5m < 50 and k_5m > d_5m
        if not in_zone:
            return False, f"STOCH_ZONE_LONG(k={k_5m:.0f},d={d_5m:.0f})"
    else:
        in_zone = k_5m > 50 and k_5m < d_5m
        if not in_zone:
            return False, f"STOCH_ZONE_SHORT(k={k_5m:.0f},d={d_5m:.0f})"
    return True, "STOCH_ZONE_OK"


def check_gate_score_threshold(ind: Dict, is_long: bool, threshold: int = 12) -> Tuple[bool, int, str]:
    """Gate: Entry score >= threshold (simplified scoring)."""
    score = 0
    # WT composite
    wt1_1h = _sf(ind.get("wt1_1h"), 0)
    wt2_1h = _sf(ind.get("wt2_1h"), 0)
    wt1_4h = _sf(ind.get("wt1_4h"), 0)
    wt2_4h = _sf(ind.get("wt2_4h"), 0)
    wt_cross_3m = str(ind.get("wt_cross_3m", ""))
    if is_long:
        if wt1_1h > wt2_1h:
            score += 3
        if wt1_4h > wt2_4h:
            score += 3
        if wt_cross_3m == "BULL":
            score += 5
    else:
        if wt1_1h < wt2_1h:
            score += 3
        if wt1_4h < wt2_4h:
            score += 3
        if wt_cross_3m == "BEAR":
            score += 5
    # DC position
    dc_pos = _sf(ind.get("dc_position_1h", ind.get("dc_position")), 0.5)
    if is_long and dc_pos < 0.25:
        score += 5
    elif not is_long and dc_pos > 0.75:
        score += 5
    # MFI
    mfi_4h = _sf(ind.get("mfi_4h", ind.get("mfi_14")), 50)
    if is_long and mfi_4h < 42:
        score += 4
    elif not is_long and mfi_4h > 58:
        score += 4
    # Stoch
    k_4h = _sf(ind.get("stoch_k_4h"), 50)
    d_4h = _sf(ind.get("stoch_d_4h"), 50)
    if is_long and k_4h < 35 and k_4h > d_4h:
        score += 4
    elif not is_long and k_4h > 65 and k_4h < d_4h:
        score += 4
    # ADX trending
    adx = _sf(ind.get("adx_14", ind.get("adx_1h")), 25)
    if adx > 25:
        score += 2
    if score >= threshold:
        return True, score, f"SCORE_OK({score}>={threshold})"
    return False, score, f"SCORE_LOW({score}<{threshold})"


# ═══════════════════════════════════════════════════════════════════
# MAIN ANALYSIS
# ═══════════════════════════════════════════════════════════════════

ALL_GATES = [
    ("LTF_ALIGNMENT", check_gate_ltf_alignment),
    ("HTF_ALIGNMENT", check_gate_htf_alignment),
    ("DAILY_MANDATORY", check_gate_daily_mandatory),
    ("K3M_EXHAUSTION", check_gate_k3m_exhaustion),
    ("WT_CROSS_CONCURRENT", check_gate_wt_cross),
    ("DC_STRUCTURE", check_gate_dc_structure),
    ("ADX_REGIME", check_gate_adx_regime),
    ("MFI_DIRECTION", check_gate_mfi_direction),
    ("STOCH_ENTRY_ZONE", check_gate_stoch_entry_zone),
]


def analyze_entries(entries: List[Dict], symbol_filter: str = None) -> Dict:
    """Run all winning entries through each gate. Returns gate rejection stats."""
    gate_stats = {name: {"passed": 0, "failed": 0, "reasons": Counter()} for name, _ in ALL_GATES}
    gate_stats["SCORE_THRESHOLD"] = {"passed": 0, "failed": 0, "score_dist": [], "reasons": Counter()}
    total_checked = 0
    total_all_pass = 0
    per_trade_results = []
    npz_cache = {}
    symbols_found = set()
    symbols_missing = set()
    for entry in entries:
        symbol = (entry.get("symbol", "") or "").upper().replace("/", "").replace("-", "")
        if not symbol:
            continue
        if symbol_filter and symbol != symbol_filter.upper():
            continue
        side = (entry.get("side", "") or "").upper()
        if side not in ("LONG", "SHORT"):
            continue
        is_long = side == "LONG"
        # Parse entry time
        entry_time = entry.get("entry_time", "")
        try:
            if isinstance(entry_time, str) and entry_time:
                et = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            else:
                continue
            target_ts = int(et.timestamp())
        except Exception:
            continue
        # Load NPZ
        if symbol not in npz_cache:
            npz_cache[symbol] = load_npz(symbol)
        npz = npz_cache[symbol]
        if npz is None:
            symbols_missing.add(symbol)
            continue
        symbols_found.add(symbol)
        # Get indicators at entry time
        ind = get_indicators_at_time(npz, target_ts)
        if not ind:
            continue
        total_checked += 1
        trade_result = {"symbol": symbol, "side": side, "entry_time": entry_time, "pnl": float(entry.get("pnl", 0)), "gates": {}}
        all_pass = True
        # Check each gate
        for gate_name, gate_func in ALL_GATES:
            if gate_name == "ADX_REGIME":
                passed, reason = gate_func(ind)
            elif gate_name in ("MFI_DIRECTION", "STOCH_ENTRY_ZONE"):
                passed, reason = gate_func(ind, is_long)
            else:
                passed, reason = gate_func(ind, is_long)
            trade_result["gates"][gate_name] = {"passed": passed, "reason": reason}
            if passed:
                gate_stats[gate_name]["passed"] += 1
            else:
                gate_stats[gate_name]["failed"] += 1
                gate_stats[gate_name]["reasons"][reason] += 1
                all_pass = False
        # Score threshold
        score_pass, score, score_reason = check_gate_score_threshold(ind, is_long)
        trade_result["gates"]["SCORE_THRESHOLD"] = {"passed": score_pass, "reason": score_reason, "score": score}
        gate_stats["SCORE_THRESHOLD"]["score_dist"].append(score)
        if score_pass:
            gate_stats["SCORE_THRESHOLD"]["passed"] += 1
        else:
            gate_stats["SCORE_THRESHOLD"]["failed"] += 1
            gate_stats["SCORE_THRESHOLD"]["reasons"][score_reason] += 1
            all_pass = False
        if all_pass:
            total_all_pass += 1
        per_trade_results.append(trade_result)
    return {"total_winning_trades": len(entries), "total_checked": total_checked, "total_all_gates_pass": total_all_pass, "symbols_found": sorted(symbols_found), "symbols_missing": sorted(symbols_missing), "gate_stats": gate_stats, "per_trade_results": per_trade_results}


def print_report(results: Dict):
    """Print a clear, readable gate analysis report."""
    print(f"\n{'='*80}")
    print(f"  GATE-BY-GATE ANALYSIS: WHY OUR SYSTEM MISSES PROFITABLE TRADES")
    print(f"{'='*80}")
    print(f"\n  Winning trades loaded: {results['total_winning_trades']}")
    print(f"  Matched to NPZ data:  {results['total_checked']}")
    print(f"  ALL gates pass:        {results['total_all_gates_pass']} ({results['total_all_gates_pass']/max(results['total_checked'],1)*100:.1f}%)")
    print(f"  Symbols found:         {len(results['symbols_found'])}")
    print(f"  Symbols missing NPZ:   {len(results['symbols_missing'])}")
    if results["symbols_missing"]:
        print(f"    Missing: {', '.join(results['symbols_missing'][:20])}")
    print(f"\n{'─'*80}")
    print(f"  {'GATE':<25} {'PASS':>6} {'FAIL':>6} {'KILL%':>7}  TOP REJECTION REASON")
    print(f"{'─'*80}")
    total = results["total_checked"]
    # Sort gates by kill rate (most deadly first)
    gate_order = sorted(results["gate_stats"].items(), key=lambda x: x[1]["failed"], reverse=True)
    for gate_name, stats in gate_order:
        passed = stats["passed"]
        failed = stats["failed"]
        kill_pct = failed / max(total, 1) * 100
        top_reason = ""
        if stats["reasons"]:
            top_r, top_count = stats["reasons"].most_common(1)[0]
            top_reason = f"{top_r} ({top_count})"
        bar = "█" * int(kill_pct / 2) + "░" * (50 - int(kill_pct / 2))
        marker = " *** CRITICAL" if kill_pct > 50 else " ** HIGH" if kill_pct > 30 else ""
        print(f"  {gate_name:<25} {passed:>6} {failed:>6} {kill_pct:>6.1f}%  {top_reason[:45]}{marker}")
    # Score distribution
    score_dist = results["gate_stats"]["SCORE_THRESHOLD"].get("score_dist", [])
    if score_dist:
        scores = np.array(score_dist)
        print(f"\n{'─'*80}")
        print(f"  ENTRY SCORE DISTRIBUTION (threshold=12, profitable trades only)")
        print(f"  Mean={scores.mean():.1f}  Median={np.median(scores):.0f}  P25={np.percentile(scores,25):.0f}  P75={np.percentile(scores,75):.0f}")
        print(f"  Score < 6:   {np.sum(scores < 6):>4} ({np.sum(scores < 6)/len(scores)*100:.0f}%)")
        print(f"  Score 6-11:  {np.sum((scores >= 6) & (scores < 12)):>4} ({np.sum((scores >= 6) & (scores < 12))/len(scores)*100:.0f}%)")
        print(f"  Score 12-17: {np.sum((scores >= 12) & (scores < 18)):>4} ({np.sum((scores >= 12) & (scores < 18))/len(scores)*100:.0f}%)")
        print(f"  Score >= 18: {np.sum(scores >= 18):>4} ({np.sum(scores >= 18)/len(scores)*100:.0f}%)")
    # Combination analysis: which 2-gate combos kill the most
    print(f"\n{'─'*80}")
    print(f"  GATE COMBINATION ANALYSIS (which pairs kill together)")
    combo_counts = Counter()
    for tr in results["per_trade_results"]:
        failed_gates = [g for g, v in tr["gates"].items() if not v["passed"]]
        for i in range(len(failed_gates)):
            for j in range(i + 1, len(failed_gates)):
                combo_counts[(failed_gates[i], failed_gates[j])] += 1
    for (g1, g2), count in combo_counts.most_common(10):
        print(f"  {g1} + {g2}: {count} trades blocked by BOTH ({count/max(total,1)*100:.1f}%)")
    print(f"\n{'='*80}")
    # Actionable recommendations
    print(f"\n  RECOMMENDATIONS:")
    for gate_name, stats in gate_order:
        kill_pct = stats["failed"] / max(total, 1) * 100
        if kill_pct > 40:
            print(f"  >>> {gate_name}: kills {kill_pct:.0f}% of profitable trades — INVESTIGATE LOOSENING")
        elif kill_pct > 25:
            print(f"  >>  {gate_name}: kills {kill_pct:.0f}% — consider relaxing threshold")
    print()


def export_csv(results: Dict):
    """Export per-trade gate results to CSV."""
    csv_path = RESULTS_DIR / f"gate_analysis_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    fieldnames = ["symbol", "side", "entry_time", "pnl", "all_pass"] + [g for g, _ in ALL_GATES] + ["SCORE_THRESHOLD", "score_value"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for tr in results["per_trade_results"]:
            row = {"symbol": tr["symbol"], "side": tr["side"], "entry_time": tr["entry_time"], "pnl": tr["pnl"], "all_pass": all(v["passed"] for v in tr["gates"].values())}
            for gate_name in [g for g, _ in ALL_GATES] + ["SCORE_THRESHOLD"]:
                gate_data = tr["gates"].get(gate_name, {})
                row[gate_name] = "PASS" if gate_data.get("passed") else gate_data.get("reason", "UNKNOWN")
            row["score_value"] = tr["gates"].get("SCORE_THRESHOLD", {}).get("score", 0)
            writer.writerow(row)
    logger.info(f"Exported gate analysis to {csv_path}")
    return csv_path


def main():
    parser = argparse.ArgumentParser(description="Gate-by-Gate Analysis of Profitable Trader Entries")
    parser.add_argument("--symbol", type=str, default=None, help="Filter to single symbol")
    parser.add_argument("--export", action="store_true", help="Export CSV of all gate results")
    args = parser.parse_args()
    entries = load_winning_entries()
    if not entries:
        logger.error("No winning entries to analyze")
        return
    results = analyze_entries(entries, symbol_filter=args.symbol)
    print_report(results)
    if args.export:
        export_csv(results)
    # Always save JSON results
    json_path = RESULTS_DIR / f"gate_analysis_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"
    save_data = {k: v for k, v in results.items() if k != "per_trade_results"}
    # Convert Counter objects
    for gate_name in save_data.get("gate_stats", {}):
        reasons = save_data["gate_stats"][gate_name].get("reasons")
        if isinstance(reasons, Counter):
            save_data["gate_stats"][gate_name]["reasons"] = dict(reasons)
        score_dist = save_data["gate_stats"][gate_name].get("score_dist")
        if isinstance(score_dist, list) and score_dist:
            arr = np.array(score_dist)
            save_data["gate_stats"][gate_name]["score_summary"] = {"mean": float(arr.mean()), "median": float(np.median(arr)), "p25": float(np.percentile(arr, 25)), "p75": float(np.percentile(arr, 75)), "min": float(arr.min()), "max": float(arr.max())}
            del save_data["gate_stats"][gate_name]["score_dist"]
    with open(json_path, "w") as f:
        json.dump(save_data, f, indent=2)
    logger.info(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
