#!/usr/bin/env python3
"""
Backtest V5 — Trade Analyzer Agent

Reads ALL V5 JSONL trade logs, analyzes every trade's entry reason, exit reason,
indicator values at decision time, and PnL. Produces a comprehensive report:
- Per entry function: win rate, avg PnL, which indicators drove winners vs losers
- Per exit function: effectiveness, premature exits, held-too-long exits
- Per indicator: correlation with winning/losing trades
- Actionable findings: what to keep, what to disable, what to tune

Usage:
    python3 backtest_v5_analyze.py                    # analyze latest run
    python3 backtest_v5_analyze.py --mode crypto      # crypto only
    python3 backtest_v5_analyze.py --mode tradier      # stocks only
    python3 backtest_v5_analyze.py --log-file path.jsonl  # specific log file
"""

import argparse
import json
import os
import platform
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance-sandbox")

V5_LOGS_DIR = BASE_PATH / "backtest_v5" / "logs"
V5_RESULTS_DIR = BASE_PATH / "backtest_v5" / "results"
V5_REPORTS_DIR = BASE_PATH / "backtest_v5" / "reports"


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------
class Trade:
    """Paired OPEN+CLOSE for a single position lifecycle."""

    def __init__(self):
        self.symbol: str = ""
        self.side: str = ""
        self.entry_ts: int = 0
        self.exit_ts: int = 0
        self.entry_bar: int = 0
        self.exit_bar: int = 0
        self.entry_price: float = 0.0
        self.exit_price: float = 0.0
        self.entry_reason: str = ""
        self.exit_reason: str = ""
        self.entry_func: str = ""
        self.exit_func: str = ""
        self.gain_pct: float = 0.0
        self.pnl_usd: float = 0.0
        self.bars_held: int = 0
        self.max_gain: float = 0.0
        self.augment_count: int = 0
        self.was_reduced: bool = False
        self.entry_indicators: Dict[str, Any] = {}
        self.exit_indicators: Dict[str, Any] = {}
        self.entry_decision_path: List[str] = []
        self.exit_decision_path: List[str] = []
        self.reduces: List[Dict] = []
        self.augments: List[Dict] = []

    @property
    def is_winner(self) -> bool:
        return self.gain_pct > 0

    @property
    def hold_minutes(self) -> float:
        return self.bars_held * 15.0

    @property
    def hold_hours(self) -> float:
        return self.hold_minutes / 60.0


# ---------------------------------------------------------------------------
# Log parser
# ---------------------------------------------------------------------------
def load_logs(log_path: str) -> List[Dict]:
    """Load JSONL trade logs."""
    logs = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                logs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return logs


def pair_trades(logs: List[Dict]) -> List[Trade]:
    """Match OPEN logs with CLOSE/REDUCE exit events.
    Each REDUCE and CLOSE becomes its own Trade for analysis — this captures
    EVERY exit decision as an analyzable event, not just full closes."""
    trades = []
    actions_by_key = defaultdict(list)
    for log in logs:
        pk = f"{log['symbol']}_{log['side']}"
        actions_by_key[pk].append(log)
    for pk, actions in actions_by_key.items():
        actions.sort(key=lambda x: x.get("bar_idx", x.get("timestamp", 0)))
        current_open = None
        for act in actions:
            if act["action"] == "OPEN":
                current_open = act
            elif act["action"] in ("REDUCE", "CLOSE", "QUICK_CLOSE"):
                if current_open is None:
                    continue
                t = Trade()
                t.symbol = current_open.get("symbol", "")
                t.side = current_open.get("side", current_open.get("position_side", ""))
                t.entry_ts = current_open.get("timestamp", 0)
                t.exit_ts = act.get("timestamp", 0)
                t.entry_bar = current_open.get("bar_idx", 0)
                t.exit_bar = act.get("bar_idx", 0)
                t.entry_price = current_open.get("price", 0)
                t.exit_price = act.get("price", 0)
                t.entry_reason = current_open.get("reason", "")
                t.exit_reason = act.get("reason", "")
                t.entry_func = current_open.get("function_path", current_open.get("action", ""))
                t.exit_func = act.get("function_path", act.get("action", ""))
                # Calculate gain from prices if not provided
                if "gain_pct" in act:
                    t.gain_pct = act["gain_pct"]
                elif t.entry_price > 0 and t.exit_price > 0:
                    if t.side in ("LONG", "BUY"):
                        t.gain_pct = (t.exit_price - t.entry_price) / t.entry_price * 100
                    else:
                        t.gain_pct = (t.entry_price - t.exit_price) / t.entry_price * 100
                t.pnl_usd = act.get("pnl_usd", t.gain_pct * abs(act.get("quantity", 0)) * t.entry_price / 100 if t.entry_price > 0 else 0)
                t.bars_held = max(0, t.exit_bar - t.entry_bar) if t.exit_bar and t.entry_bar else int((t.exit_ts - t.entry_ts) / 900) if t.exit_ts > t.entry_ts else 0
                t.max_gain = act.get("position_state", {}).get("max_gain", t.gain_pct)
                t.augment_count = act.get("position_state", {}).get("augment_count", 0)
                t.was_reduced = act.get("position_state", {}).get("was_reduced", False)
                t.entry_indicators = current_open.get("indicators_checked", current_open.get("indicators_snapshot", {}))
                t.exit_indicators = act.get("indicators_checked", act.get("indicators_snapshot", {}))
                t.entry_decision_path = current_open.get("decision_path", [])
                t.exit_decision_path = act.get("decision_path", [])
                trades.append(t)
                if act["action"] == "CLOSE":
                    current_open = None
    return trades


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------
def analyze_by_entry_function(trades: List[Trade]) -> Dict[str, Dict]:
    """Group trades by entry function and compute stats."""
    groups = defaultdict(list)
    for t in trades:
        groups[t.entry_func].append(t)
    results = {}
    for func, tlist in sorted(groups.items(), key=lambda x: -len(x[1])):
        gains = [t.gain_pct for t in tlist]
        pnls = [t.pnl_usd for t in tlist]
        wins = [t for t in tlist if t.is_winner]
        holds = [t.hold_hours for t in tlist]
        results[func] = {
            "count": len(tlist),
            "win_rate": round(len(wins) / max(1, len(tlist)) * 100, 1),
            "avg_gain": round(np.mean(gains), 3),
            "median_gain": round(np.median(gains), 3),
            "std_gain": round(np.std(gains), 3),
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(np.mean(pnls), 2),
            "avg_hold_hours": round(np.mean(holds), 1),
            "max_gain": round(max(gains), 2) if gains else 0,
            "max_loss": round(min(gains), 2) if gains else 0,
            "sharpe": round(np.mean(gains) / max(0.001, np.std(gains)), 3) if gains else 0,
        }
    return results


def analyze_by_exit_function(trades: List[Trade]) -> Dict[str, Dict]:
    """Group trades by exit function and compute stats."""
    groups = defaultdict(list)
    for t in trades:
        groups[t.exit_func].append(t)
    results = {}
    for func, tlist in sorted(groups.items(), key=lambda x: -len(x[1])):
        gains = [t.gain_pct for t in tlist]
        pnls = [t.pnl_usd for t in tlist]
        max_gains = [t.max_gain for t in tlist]
        # "Premature" = exited with max_gain >> final gain (left money on table)
        premature = [t for t in tlist if t.max_gain > t.gain_pct + 2.0 and t.gain_pct > 0]
        # "Held too long" = max_gain was high but ended negative
        held_too_long = [t for t in tlist if t.max_gain > 3.0 and t.gain_pct <= 0]
        results[func] = {
            "count": len(tlist),
            "win_rate": round(len([t for t in tlist if t.is_winner]) / max(1, len(tlist)) * 100, 1),
            "avg_gain": round(np.mean(gains), 3),
            "total_pnl": round(sum(pnls), 2),
            "avg_max_gain": round(np.mean(max_gains), 2) if max_gains else 0,
            "premature_exits": len(premature),
            "held_too_long": len(held_too_long),
            "avg_gain_left_on_table": round(np.mean([t.max_gain - t.gain_pct for t in premature]), 2) if premature else 0,
        }
    return results


def analyze_indicator_correlations(trades: List[Trade]) -> Dict[str, Dict]:
    """For each indicator checked at entry, correlate with trade outcome."""
    indicator_outcomes = defaultdict(lambda: {"winner_values": [], "loser_values": []})
    for t in trades:
        for key, val in t.entry_indicators.items():
            if isinstance(val, (int, float)) and not np.isnan(val):
                if t.is_winner:
                    indicator_outcomes[key]["winner_values"].append(val)
                else:
                    indicator_outcomes[key]["loser_values"].append(val)
    results = {}
    for ind, data in sorted(indicator_outcomes.items()):
        w = data["winner_values"]
        l = data["loser_values"]
        if len(w) < 10 or len(l) < 10:
            continue
        w_mean = np.mean(w)
        l_mean = np.mean(l)
        separation = abs(w_mean - l_mean) / max(0.001, (np.std(w) + np.std(l)) / 2)
        results[ind] = {
            "n_winners": len(w),
            "n_losers": len(l),
            "winner_mean": round(w_mean, 4),
            "loser_mean": round(l_mean, 4),
            "winner_median": round(np.median(w), 4),
            "loser_median": round(np.median(l), 4),
            "separation_score": round(separation, 3),
            "direction": "HIGHER_WINS" if w_mean > l_mean else "LOWER_WINS",
        }
    # Sort by separation score
    results = dict(sorted(results.items(), key=lambda x: -x[1]["separation_score"]))
    return results


def analyze_decision_paths(trades: List[Trade]) -> Dict[str, Dict]:
    """Analyze which decision path elements correlate with wins/losses."""
    path_outcomes = defaultdict(lambda: {"wins": 0, "losses": 0, "gains": []})
    for t in trades:
        for step in t.entry_decision_path:
            # Extract the tag (before parentheses)
            tag = step.split("(")[0].strip()
            if t.is_winner:
                path_outcomes[tag]["wins"] += 1
            else:
                path_outcomes[tag]["losses"] += 1
            path_outcomes[tag]["gains"].append(t.gain_pct)
    results = {}
    for tag, data in sorted(path_outcomes.items(), key=lambda x: -(x[1]["wins"] + x[1]["losses"])):
        total = data["wins"] + data["losses"]
        if total < 5:
            continue
        results[tag] = {
            "count": total,
            "win_rate": round(data["wins"] / total * 100, 1),
            "avg_gain": round(np.mean(data["gains"]), 3),
        }
    return results


def analyze_time_patterns(trades: List[Trade]) -> Dict[str, Dict]:
    """Analyze trade performance by time of day, day of week, hold duration."""
    hour_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "gains": []})
    hold_buckets = {"<1h": [], "1-4h": [], "4-12h": [], "12-48h": [], ">48h": []}
    for t in trades:
        hour = (t.entry_ts % 86400) // 3600
        if t.is_winner:
            hour_stats[hour]["wins"] += 1
        else:
            hour_stats[hour]["losses"] += 1
        hour_stats[hour]["gains"].append(t.gain_pct)
        h = t.hold_hours
        if h < 1:
            hold_buckets["<1h"].append(t)
        elif h < 4:
            hold_buckets["1-4h"].append(t)
        elif h < 12:
            hold_buckets["4-12h"].append(t)
        elif h < 48:
            hold_buckets["12-48h"].append(t)
        else:
            hold_buckets[">48h"].append(t)
    results = {"by_hour": {}, "by_hold_duration": {}}
    for hour in sorted(hour_stats.keys()):
        data = hour_stats[hour]
        total = data["wins"] + data["losses"]
        results["by_hour"][f"{int(hour):02d}:00 UTC"] = {
            "count": total,
            "win_rate": round(data["wins"] / total * 100, 1),
            "avg_gain": round(np.mean(data["gains"]), 3),
        }
    for bucket, tlist in hold_buckets.items():
        if not tlist:
            results["by_hold_duration"][bucket] = {"count": 0, "win_rate": 0, "avg_gain": 0}
            continue
        wins = sum(1 for t in tlist if t.is_winner)
        results["by_hold_duration"][bucket] = {
            "count": len(tlist),
            "win_rate": round(wins / len(tlist) * 100, 1),
            "avg_gain": round(np.mean([t.gain_pct for t in tlist]), 3),
        }
    return results


def analyze_symbol_performance(trades: List[Trade]) -> Dict[str, Dict]:
    """Per-symbol stats."""
    groups = defaultdict(list)
    for t in trades:
        groups[t.symbol].append(t)
    results = {}
    for sym, tlist in sorted(groups.items(), key=lambda x: -sum(t.pnl_usd for t in x[1])):
        gains = [t.gain_pct for t in tlist]
        pnls = [t.pnl_usd for t in tlist]
        wins = sum(1 for t in tlist if t.is_winner)
        results[sym] = {
            "count": len(tlist),
            "win_rate": round(wins / max(1, len(tlist)) * 100, 1),
            "avg_gain": round(np.mean(gains), 3),
            "total_pnl": round(sum(pnls), 2),
            "best_trade": round(max(gains), 2),
            "worst_trade": round(min(gains), 2),
        }
    return results


def generate_actionable_findings(entry_stats: Dict, exit_stats: Dict, indicator_stats: Dict, path_stats: Dict) -> List[str]:
    """Generate specific recommendations."""
    findings = []
    # Entry functions that lose money
    for func, stats in entry_stats.items():
        if stats["count"] >= 20 and stats["win_rate"] < 40:
            findings.append(f"DISABLE {func}: {stats['count']} trades, {stats['win_rate']}% WR, ${stats['total_pnl']:.0f} total PnL — losing money consistently")
        elif stats["count"] >= 20 and stats["win_rate"] > 65:
            findings.append(f"KEEP {func}: {stats['count']} trades, {stats['win_rate']}% WR, ${stats['total_pnl']:.0f} total PnL — reliable winner")
    # Exit functions leaving money on table
    for func, stats in exit_stats.items():
        if stats["premature_exits"] > stats["count"] * 0.3 and stats["count"] >= 10:
            findings.append(f"TUNE {func}: {stats['premature_exits']}/{stats['count']} exits left avg {stats['avg_gain_left_on_table']:.1f}% on table — consider widening thresholds")
        if stats["held_too_long"] > stats["count"] * 0.2 and stats["count"] >= 10:
            findings.append(f"TIGHTEN {func}: {stats['held_too_long']}/{stats['count']} trades peaked then went negative — exit sooner")
    # Indicator edges
    for ind, stats in list(indicator_stats.items())[:10]:
        if stats["separation_score"] > 0.5:
            findings.append(f"STRONG EDGE {ind}: winners avg {stats['winner_mean']:.2f} vs losers {stats['loser_mean']:.2f} (separation={stats['separation_score']:.2f}) — {stats['direction']}")
    # Decision path items that predict losses
    for tag, stats in path_stats.items():
        if stats["count"] >= 20 and stats["win_rate"] < 35:
            findings.append(f"RED FLAG path '{tag}': {stats['count']} trades, {stats['win_rate']}% WR — when this fires, trade usually loses")
        elif stats["count"] >= 20 and stats["win_rate"] > 70:
            findings.append(f"GREEN FLAG path '{tag}': {stats['count']} trades, {stats['win_rate']}% WR — strong positive signal")
    return findings


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------
def generate_report(trades: List[Trade], mode: str, output_dir: Path):
    """Generate comprehensive analysis report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if not trades:
        print("No trades to analyze!")
        return
    print(f"\nAnalyzing {len(trades)} trades ({mode})...")
    entry_stats = analyze_by_entry_function(trades)
    exit_stats = analyze_by_exit_function(trades)
    indicator_stats = analyze_indicator_correlations(trades)
    path_stats = analyze_decision_paths(trades)
    time_stats = analyze_time_patterns(trades)
    symbol_stats = analyze_symbol_performance(trades)
    findings = generate_actionable_findings(entry_stats, exit_stats, indicator_stats, path_stats)
    # Build report
    report_lines = []
    report_lines.append(f"# V5 Backtest Analysis Report — {mode.upper()}")
    report_lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    report_lines.append(f"Total trades analyzed: {len(trades)}")
    report_lines.append(f"Winners: {sum(1 for t in trades if t.is_winner)} ({sum(1 for t in trades if t.is_winner)/len(trades)*100:.1f}%)")
    report_lines.append(f"Total PnL: ${sum(t.pnl_usd for t in trades):.2f}")
    report_lines.append("")
    # Actionable findings
    report_lines.append("## ACTIONABLE FINDINGS (Most Important)")
    report_lines.append("")
    for i, f in enumerate(findings, 1):
        report_lines.append(f"{i}. {f}")
    report_lines.append("")
    # Entry function analysis
    report_lines.append("## Entry Function Performance")
    report_lines.append("")
    report_lines.append("| Function | Count | WR% | Avg Gain | Sharpe | Total PnL | Avg Hold |")
    report_lines.append("|----------|-------|-----|----------|--------|-----------|----------|")
    for func, s in sorted(entry_stats.items(), key=lambda x: -x[1]["total_pnl"]):
        report_lines.append(f"| {func} | {s['count']} | {s['win_rate']}% | {s['avg_gain']:+.2f}% | {s['sharpe']:.2f} | ${s['total_pnl']:.0f} | {s['avg_hold_hours']:.1f}h |")
    report_lines.append("")
    # Exit function analysis
    report_lines.append("## Exit Function Effectiveness")
    report_lines.append("")
    report_lines.append("| Function | Count | WR% | Avg Gain | Total PnL | Premature | Held Too Long | Avg Left on Table |")
    report_lines.append("|----------|-------|-----|----------|-----------|-----------|---------------|-------------------|")
    for func, s in sorted(exit_stats.items(), key=lambda x: -x[1]["count"]):
        report_lines.append(f"| {func} | {s['count']} | {s['win_rate']}% | {s['avg_gain']:+.2f}% | ${s['total_pnl']:.0f} | {s['premature_exits']} | {s['held_too_long']} | {s['avg_gain_left_on_table']:.1f}% |")
    report_lines.append("")
    # Top indicator edges
    report_lines.append("## Top Indicator Edges (by separation score)")
    report_lines.append("")
    report_lines.append("| Indicator | Separation | Winner Mean | Loser Mean | Direction | n_win | n_lose |")
    report_lines.append("|-----------|------------|-------------|------------|-----------|-------|--------|")
    for ind, s in list(indicator_stats.items())[:25]:
        report_lines.append(f"| {ind} | {s['separation_score']:.3f} | {s['winner_mean']:.4f} | {s['loser_mean']:.4f} | {s['direction']} | {s['n_winners']} | {s['n_losers']} |")
    report_lines.append("")
    # Decision path analysis
    report_lines.append("## Decision Path Analysis")
    report_lines.append("")
    report_lines.append("| Path Tag | Count | WR% | Avg Gain |")
    report_lines.append("|----------|-------|-----|----------|")
    for tag, s in sorted(path_stats.items(), key=lambda x: -x[1]["count"])[:30]:
        report_lines.append(f"| {tag} | {s['count']} | {s['win_rate']}% | {s['avg_gain']:+.3f}% |")
    report_lines.append("")
    # Time patterns
    report_lines.append("## Time Patterns")
    report_lines.append("")
    report_lines.append("### By Entry Hour (UTC)")
    report_lines.append("| Hour | Count | WR% | Avg Gain |")
    report_lines.append("|------|-------|-----|----------|")
    for hour, s in time_stats["by_hour"].items():
        report_lines.append(f"| {hour} | {s['count']} | {s['win_rate']}% | {s['avg_gain']:+.3f}% |")
    report_lines.append("")
    report_lines.append("### By Hold Duration")
    report_lines.append("| Duration | Count | WR% | Avg Gain |")
    report_lines.append("|----------|-------|-----|----------|")
    for bucket, s in time_stats["by_hold_duration"].items():
        report_lines.append(f"| {bucket} | {s['count']} | {s['win_rate']}% | {s['avg_gain']:+.3f}% |")
    report_lines.append("")
    # Top/bottom symbols
    report_lines.append("## Symbol Performance (Top 10 / Bottom 10)")
    report_lines.append("")
    report_lines.append("| Symbol | Trades | WR% | Avg Gain | Total PnL | Best | Worst |")
    report_lines.append("|--------|--------|-----|----------|-----------|------|-------|")
    sym_list = list(symbol_stats.items())
    for sym, s in sym_list[:10]:
        report_lines.append(f"| {sym} | {s['count']} | {s['win_rate']}% | {s['avg_gain']:+.2f}% | ${s['total_pnl']:.0f} | {s['best_trade']:.1f}% | {s['worst_trade']:.1f}% |")
    if len(sym_list) > 20:
        report_lines.append("| ... | ... | ... | ... | ... | ... | ... |")
        for sym, s in sym_list[-10:]:
            report_lines.append(f"| {sym} | {s['count']} | {s['win_rate']}% | {s['avg_gain']:+.2f}% | ${s['total_pnl']:.0f} | {s['best_trade']:.1f}% | {s['worst_trade']:.1f}% |")
    report_lines.append("")
    # Write report
    report_text = "\n".join(report_lines)
    report_path = output_dir / f"analysis_{mode}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.md"
    with open(report_path, "w") as f:
        f.write(report_text)
    print(f"\nReport saved to {report_path}")
    # Also write JSON for programmatic access
    json_data = {
        "mode": mode,
        "n_trades": len(trades),
        "entry_functions": entry_stats,
        "exit_functions": exit_stats,
        "indicator_edges": dict(list(indicator_stats.items())[:50]),
        "decision_paths": path_stats,
        "time_patterns": time_stats,
        "symbol_performance": symbol_stats,
        "actionable_findings": findings,
    }
    json_path = output_dir / f"analysis_{mode}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2, default=str)
    # Print summary to terminal
    print("\n" + "=" * 80)
    print(f"  V5 ANALYSIS REPORT — {mode.upper()}")
    print("=" * 80)
    print(f"\n  ACTIONABLE FINDINGS ({len(findings)} items):\n")
    for i, finding in enumerate(findings, 1):
        print(f"  {i}. {finding}")
    print(f"\n  ENTRY FUNCTIONS (by PnL):")
    for func, s in sorted(entry_stats.items(), key=lambda x: -x[1]["total_pnl"])[:10]:
        marker = "+" if s["total_pnl"] > 0 else "-"
        print(f"    [{marker}] {func:45s}  n={s['count']:4d}  WR={s['win_rate']:5.1f}%  PnL=${s['total_pnl']:8.0f}  Sharpe={s['sharpe']:.2f}")
    print(f"\n  EXIT FUNCTIONS (by count):")
    for func, s in sorted(exit_stats.items(), key=lambda x: -x[1]["count"])[:10]:
        print(f"    {func:45s}  n={s['count']:4d}  WR={s['win_rate']:5.1f}%  premature={s['premature_exits']:3d}  held_too_long={s['held_too_long']:3d}")
    print(f"\n  TOP INDICATOR EDGES:")
    for ind, s in list(indicator_stats.items())[:10]:
        print(f"    {ind:30s}  sep={s['separation_score']:.3f}  win_avg={s['winner_mean']:8.4f}  lose_avg={s['loser_mean']:8.4f}  {s['direction']}")
    print("=" * 80)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="V5 Backtest Trade Analyzer")
    parser.add_argument("--mode", choices=["crypto", "tradier"], default=None, help="Filter by mode")
    parser.add_argument("--log-file", type=str, default=None, help="Specific JSONL file to analyze")
    args = parser.parse_args()
    # Find log files
    if args.log_file:
        log_files = [Path(args.log_file)]
    else:
        if not V5_LOGS_DIR.exists():
            print(f"No V5 logs directory found at {V5_LOGS_DIR}")
            sys.exit(1)
        log_files = sorted(V5_LOGS_DIR.glob("trades_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if args.mode:
            log_files = [f for f in log_files if args.mode in f.name]
    if not log_files:
        print("No log files found!")
        sys.exit(1)
    # Use latest or all
    print(f"Found {len(log_files)} log files")
    all_logs = []
    for lf in log_files[:5]:  # analyze up to 5 most recent
        print(f"  Loading {lf.name}...")
        logs = load_logs(str(lf))
        all_logs.extend(logs)
        print(f"    {len(logs)} entries")
    if not all_logs:
        print("No log entries found!")
        sys.exit(1)
    # Pair trades
    trades = pair_trades(all_logs)
    print(f"\nPaired {len(trades)} complete trades from {len(all_logs)} log entries")
    # Determine mode from data
    mode = args.mode or ("crypto" if any("USDT" in t.symbol for t in trades) else "tradier")
    # Generate report
    V5_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    generate_report(trades, mode, V5_REPORTS_DIR)


if __name__ == "__main__":
    main()
