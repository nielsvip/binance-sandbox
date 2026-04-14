#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""Persistent Master Traders Record — survives everything.

Maintains two permanent artifacts:
1. data/master_traders_record.xlsx — multi-sheet Excel with:
   - TRADERS: every trader we've ever seen, with stats and history
   - PATTERNS: every high-WR pattern extracted, with source and date
   - GATE_ANALYSIS: what our gates block vs what masters do
   - TEST_QUEUE: V8 test configs derived from master insights
   - SWEEP_LOG: every sweep cycle logged with timestamp

2. data/master_traders_record.csv — flat CSV backup of TRADERS sheet

Called by trader_sweep_orchestrator.py after every cycle.
Also runnable standalone to rebuild from all historical data.

Usage:
    python3 trader_masters_record.py                    # Rebuild from all data
    python3 trader_masters_record.py --inject-tests     # Generate V8 test configs
    python3 trader_masters_record.py --show             # Print summary
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
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = config.DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("masters_record")

RECORD_XLSX = DATA_DIR / "master_traders_record.xlsx"
RECORD_CSV = DATA_DIR / "master_traders_record.csv"
TEST_QUEUE_JSON = DATA_DIR / "v8_test_queue.json"

EXCHANGE_DIRS = {
    "bitget": DATA_DIR / "bitget_traders",
    "bybit": DATA_DIR / "bybit_traders",
    "okx": DATA_DIR / "okx_traders",
    "binance": DATA_DIR / "binance_leaderboard",
}


# ═══════════════════════════════════════════════════════════════════
# COLLECT ALL TRADER DATA FROM ALL SOURCES
# ═══════════════════════════════════════════════════════════════════

def collect_all_traders() -> List[Dict]:
    """Gather every trader we've ever seen across all exchanges."""
    traders = {}
    # Bybit (Playwright discovered)
    pw_file = DATA_DIR / "bybit_traders" / "discovered_traders_pw.json"
    if pw_file.exists():
        for t in json.loads(pw_file.read_text()):
            uid = t.get("uid", "")
            if uid:
                traders[f"bybit_{uid}"] = {"exchange": "Bybit", "uid": uid, "nickname": t.get("nickname", ""), "roi_30d": t.get("roi", 0), "win_rate": t.get("winRate", 0), "sharpe": t.get("sharpe", 0), "drawdown": t.get("drawdown", 0), "followers": t.get("followers", 0), "pnl": t.get("pnl", t.get("follower_profit", 0)), "level": t.get("level", ""), "source": t.get("source", "bybit_pw"), "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    # Bitget (discovered)
    bg_file = DATA_DIR / "bitget_traders" / "discovered_traders.json"
    if bg_file.exists():
        for t in json.loads(bg_file.read_text()):
            uid = t.get("uid", t.get("trader_id", ""))
            if uid:
                traders[f"bitget_{uid}"] = {"exchange": "Bitget", "uid": uid, "nickname": t.get("nickname", t.get("name", "")), "roi_30d": float(t.get("roi30d", t.get("roi", 0))), "win_rate": float(t.get("winRate", t.get("win_rate", 0))), "sharpe": 0, "drawdown": float(t.get("maxDrawdown", t.get("mdd", 0))), "followers": int(t.get("followerCount", t.get("followers", 0))), "pnl": float(t.get("pnl30d", t.get("pnl", 0))), "level": "", "source": "bitget_api", "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    # OKX (from cycle summaries)
    okx_summary = DATA_DIR / "okx_traders" / "cycle_summary.jsonl"
    if okx_summary.exists():
        seen_okx = set()
        with open(okx_summary) as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    for t in data.get("top_traders", []):
                        uid = t.get("uid", "")
                        if uid and uid not in seen_okx:
                            seen_okx.add(uid)
                            traders[f"okx_{uid}"] = {"exchange": "OKX", "uid": uid, "nickname": t.get("nickname", t.get("name", "")), "roi_30d": float(t.get("roi", 0)), "win_rate": float(t.get("wr", 0)), "sharpe": 0, "drawdown": 0, "followers": 0, "pnl": float(t.get("total_pnl", 0)), "level": "", "source": "okx_scraper", "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
                except Exception:
                    continue
    # Deep analyzer health scores
    analysis_dir = DATA_DIR / "trader_analysis"
    if analysis_dir.exists():
        for f in sorted(analysis_dir.glob("trader_health_*.json")):
            try:
                health = json.loads(f.read_text())
                for uid, h in health.items():
                    key = f"analyzed_{uid}"
                    if key not in traders:
                        traders[key] = {"exchange": "multi", "uid": uid, "nickname": uid[:16], "roi_30d": 0, "win_rate": float(h.get("win_rate", 0)), "sharpe": 0, "drawdown": 0, "followers": 0, "pnl": float(h.get("total_pnl", 0)), "level": h.get("status", ""), "source": "deep_analyzer", "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
            except Exception:
                continue
    return list(traders.values())


def collect_all_patterns() -> List[Dict]:
    """Gather every high-WR pattern we've ever extracted."""
    patterns = []
    # From sweep findings
    for f in sorted((DATA_DIR / "trader_research_reports").glob("*findings*.json")):
        try:
            data = json.loads(f.read_text())
            timestamp = data.get("timestamp", f.stem)
            for p in data.get("top_patterns", []):
                patterns.append({"rule": p.get("rule", ""), "win_rate": float(p.get("wr", 0)) * 100 if float(p.get("wr", 0)) < 1 else float(p.get("wr", 0)), "n_trades": int(p.get("n", 0)), "side": p.get("side", ""), "avg_pnl_pct": float(p.get("avg_pnl_pct", 0)), "found_date": timestamp[:10], "source": f.name})
        except Exception:
            continue
    # From sweep reports
    for f in sorted((DATA_DIR / "trader_research_reports").glob("sweep_findings*.json")):
        try:
            data = json.loads(f.read_text())
            timestamp = data.get("timestamp", f.stem)
            for p in data.get("top_patterns", []):
                patterns.append({"rule": p.get("rule", ""), "win_rate": float(p.get("wr", 0)) * 100 if float(p.get("wr", 0)) < 1 else float(p.get("wr", 0)), "n_trades": int(p.get("n", 0)), "side": p.get("side", ""), "avg_pnl_pct": float(p.get("avg_pnl_pct", 0)), "found_date": timestamp[:10], "source": f.name})
        except Exception:
            continue
    # Dedupe by rule
    seen = set()
    unique = []
    for p in patterns:
        key = p["rule"]
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def collect_gate_analysis() -> List[Dict]:
    """Latest gate analysis results."""
    gate_dir = DATA_DIR / "gate_analysis"
    if not gate_dir.exists():
        return []
    jsons = sorted(gate_dir.glob("gate_analysis_*.json"), reverse=True)
    if not jsons:
        return []
    data = json.loads(jsons[0].read_text())
    rows = []
    for gate_name, stats in data.get("gate_stats", {}).items():
        total = stats.get("passed", 0) + stats.get("failed", 0)
        kill_pct = stats["failed"] / max(total, 1) * 100
        top_reason = ""
        reasons = stats.get("reasons", {})
        if reasons:
            top_reason = max(reasons, key=reasons.get)
        row = {"gate": gate_name, "passed": stats.get("passed", 0), "failed": stats.get("failed", 0), "kill_pct": round(kill_pct, 1), "top_reason": top_reason}
        if "score_summary" in stats:
            ss = stats["score_summary"]
            row["score_mean"] = ss.get("mean", 0)
            row["score_median"] = ss.get("median", 0)
        rows.append(row)
    return sorted(rows, key=lambda x: x["kill_pct"], reverse=True)


def collect_sweep_log() -> List[Dict]:
    """Collect all sweep cycle timestamps and results."""
    logs = []
    for f in sorted((DATA_DIR / "trader_research_reports").glob("*findings*.json")):
        try:
            data = json.loads(f.read_text())
            logs.append({"timestamp": data.get("timestamp", "")[:19], "trades": data.get("n_trades", 0), "enriched": data.get("n_enriched", 0), "traders": data.get("n_traders", 0), "wr": data.get("overall_wr", 0), "pnl": data.get("total_pnl", 0), "patterns": data.get("n_patterns", 0), "source": f.name})
        except Exception:
            continue
    return logs


# ═══════════════════════════════════════════════════════════════════
# GENERATE V8 TEST CONFIGS FROM PATTERNS
# ═══════════════════════════════════════════════════════════════════

def generate_test_configs(patterns: List[Dict], gate_analysis: List[Dict]) -> List[Dict]:
    """Generate V8 test configurations based on master trader insights."""
    tests = []
    # Test 1: Based on gate analysis — loosen worst killers
    killer_gates = [g for g in gate_analysis if g["kill_pct"] > 40]
    if killer_gates:
        tests.append({"test_id": "MT_T1_SCORE_6", "description": "Lower entry score threshold from 12 to 6 (captures 72% more winners)", "config_changes": {"MIN_ENTRY_SCORE": 6}, "rationale": f"SCORE_THRESHOLD kills {[g for g in gate_analysis if g['gate']=='SCORE_THRESHOLD'][0]['kill_pct'] if any(g['gate']=='SCORE_THRESHOLD' for g in gate_analysis) else '?'}% of profitable trades. Median winner score=6.", "priority": "HIGH", "status": "PENDING", "created": datetime.now(timezone.utc).strftime("%Y-%m-%d")})
        tests.append({"test_id": "MT_T2_K3M_WIDEN", "description": "Widen K3M exhaustion zone from 30-70 to 20-80", "config_changes": {"K3M_EXHAUSTION_LOW": 20, "K3M_EXHAUSTION_HIGH": 80}, "rationale": f"K3M_EXHAUSTION kills {[g for g in gate_analysis if g['gate']=='K3M_EXHAUSTION'][0]['kill_pct'] if any(g['gate']=='K3M_EXHAUSTION' for g in gate_analysis) else '?'}% of profitable trades. Winners enter at high momentum.", "priority": "HIGH", "status": "PENDING", "created": datetime.now(timezone.utc).strftime("%Y-%m-%d")})
        tests.append({"test_id": "MT_T3_LTF_2OF3", "description": "Relax LTF alignment from 3/3 to 2/3", "config_changes": {"LTF_ALIGNMENT_MIN": 2}, "rationale": f"LTF_ALIGNMENT kills {[g for g in gate_analysis if g['gate']=='LTF_ALIGNMENT'][0]['kill_pct'] if any(g['gate']=='LTF_ALIGNMENT' for g in gate_analysis) else '?'}% of profitable trades.", "priority": "MEDIUM", "status": "PENDING", "created": datetime.now(timezone.utc).strftime("%Y-%m-%d")})
        tests.append({"test_id": "MT_T4_DAILY_OPTIONAL", "description": "Make daily stoch alignment optional (not mandatory)", "config_changes": {"DAILY_ALIGNMENT_MANDATORY": False}, "rationale": f"DAILY_MANDATORY kills {[g for g in gate_analysis if g['gate']=='DAILY_MANDATORY'][0]['kill_pct'] if any(g['gate']=='DAILY_MANDATORY' for g in gate_analysis) else '?'}% — winners trade against daily 45% of time.", "priority": "MEDIUM", "status": "PENDING", "created": datetime.now(timezone.utc).strftime("%Y-%m-%d")})
    # Test 5: Based on top pattern
    for i, p in enumerate(patterns[:3]):
        if p["win_rate"] >= 70 and p["n_trades"] >= 20:
            tests.append({"test_id": f"MT_T{5+i}_PATTERN_{p['side']}", "description": f"Pattern: {p['rule'][:80]}", "config_changes": {"PATTERN_RULE": p["rule"], "PATTERN_SIDE": p["side"]}, "rationale": f"WR={p['win_rate']:.1f}% on {p['n_trades']} trades from master traders. Avg PnL={p['avg_pnl_pct']:.2f}%", "priority": "MEDIUM", "status": "PENDING", "created": datetime.now(timezone.utc).strftime("%Y-%m-%d")})
    # Test: Combo — most impactful changes together
    tests.append({"test_id": "MT_T_COMBO", "description": "Combined: Score=6 + K3M=20-80 + LTF=2/3 + Daily optional", "config_changes": {"MIN_ENTRY_SCORE": 6, "K3M_EXHAUSTION_LOW": 20, "K3M_EXHAUSTION_HIGH": 80, "LTF_ALIGNMENT_MIN": 2, "DAILY_ALIGNMENT_MANDATORY": False}, "rationale": "All top gate killers relaxed together. Expected to capture ~60% of winning master entries. MUST be validated by V8 — false positive risk.", "priority": "CRITICAL", "status": "PENDING", "created": datetime.now(timezone.utc).strftime("%Y-%m-%d")})
    return tests


# ═══════════════════════════════════════════════════════════════════
# WRITE XLSX WITH MULTIPLE SHEETS
# ═══════════════════════════════════════════════════════════════════

def write_xlsx(traders: List, patterns: List, gate_analysis: List, test_configs: List, sweep_log: List):
    """Write multi-sheet Excel workbook."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    wb = Workbook()
    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    thin_border = Border(bottom=Side(style="thin"))
    def _write_sheet(ws, headers, rows, col_widths=None):
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
            cell.border = thin_border
        for r, row in enumerate(rows, 2):
            for c, h in enumerate(headers, 1):
                val = row.get(h.lower().replace(" ", "_").replace("(%)", "_pct").replace("30d", "_30d"), row.get(h, ""))
                ws.cell(row=r, column=c, value=val)
        if col_widths:
            for i, w in enumerate(col_widths):
                ws.column_dimensions[chr(65 + i)].width = w
    # Sheet 1: TRADERS
    ws1 = wb.active
    ws1.title = "TRADERS"
    trader_headers = ["Exchange", "Nickname", "UID", "ROI_30d", "Win_Rate", "Sharpe", "Drawdown", "Followers", "PnL", "Level", "Source", "First_Seen"]
    _write_sheet(ws1, trader_headers, traders, [10, 25, 30, 10, 10, 8, 10, 10, 12, 15, 15, 12])
    # Color rows by Sharpe
    for r in range(2, len(traders) + 2):
        sharpe_val = ws1.cell(row=r, column=6).value
        if isinstance(sharpe_val, (int, float)):
            if sharpe_val >= 1.5:
                for c in range(1, len(trader_headers) + 1):
                    ws1.cell(row=r, column=c).fill = green_fill
            elif sharpe_val < 0:
                for c in range(1, len(trader_headers) + 1):
                    ws1.cell(row=r, column=c).fill = red_fill
    # Sheet 2: PATTERNS
    ws2 = wb.create_sheet("PATTERNS")
    pattern_headers = ["Rule", "Win_Rate", "N_Trades", "Side", "Avg_PnL_Pct", "Found_Date", "Source"]
    _write_sheet(ws2, pattern_headers, patterns, [80, 10, 10, 8, 12, 12, 25])
    # Sheet 3: GATE_ANALYSIS
    ws3 = wb.create_sheet("GATE_ANALYSIS")
    gate_headers = ["Gate", "Passed", "Failed", "Kill_Pct", "Top_Reason", "Score_Mean", "Score_Median"]
    _write_sheet(ws3, gate_headers, gate_analysis, [25, 8, 8, 10, 50, 10, 10])
    for r in range(2, len(gate_analysis) + 2):
        kill = ws3.cell(row=r, column=4).value
        if isinstance(kill, (int, float)) and kill > 50:
            for c in range(1, len(gate_headers) + 1):
                ws3.cell(row=r, column=c).fill = red_fill
    # Sheet 4: TEST_QUEUE
    ws4 = wb.create_sheet("TEST_QUEUE")
    test_headers = ["Test_ID", "Description", "Priority", "Status", "Rationale", "Created"]
    test_rows = [{"test_id": t["test_id"], "description": t["description"], "priority": t["priority"], "status": t["status"], "rationale": t["rationale"], "created": t["created"]} for t in test_configs]
    _write_sheet(ws4, test_headers, test_rows, [18, 60, 10, 10, 80, 12])
    # Sheet 5: SWEEP_LOG
    ws5 = wb.create_sheet("SWEEP_LOG")
    log_headers = ["Timestamp", "Trades", "Enriched", "Traders", "WR", "PnL", "Patterns", "Source"]
    _write_sheet(ws5, log_headers, sweep_log, [20, 8, 8, 8, 8, 12, 8, 30])
    wb.save(str(RECORD_XLSX))
    logger.info(f"Wrote {RECORD_XLSX} — {len(traders)} traders, {len(patterns)} patterns, {len(test_configs)} tests, {len(sweep_log)} sweep cycles")


def write_csv_backup(traders: List):
    """Flat CSV backup of traders sheet."""
    if not traders:
        return
    fieldnames = ["exchange", "nickname", "uid", "roi_30d", "win_rate", "sharpe", "drawdown", "followers", "pnl", "level", "source", "first_seen"]
    with open(RECORD_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(traders)
    logger.info(f"CSV backup: {RECORD_CSV}")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def rebuild_all(inject_tests: bool = False, show_only: bool = False):
    """Rebuild the complete master record from all data sources."""
    logger.info("Collecting all trader data...")
    traders = collect_all_traders()
    traders.sort(key=lambda x: x.get("sharpe", 0), reverse=True)
    logger.info(f"  {len(traders)} traders across {len(set(t['exchange'] for t in traders))} exchanges")
    patterns = collect_all_patterns()
    patterns.sort(key=lambda x: x.get("win_rate", 0), reverse=True)
    logger.info(f"  {len(patterns)} unique patterns")
    gate_analysis = collect_gate_analysis()
    logger.info(f"  {len(gate_analysis)} gate results")
    sweep_log = collect_sweep_log()
    logger.info(f"  {len(sweep_log)} sweep cycles logged")
    test_configs = generate_test_configs(patterns, gate_analysis)
    logger.info(f"  {len(test_configs)} V8 test configs generated")
    if show_only:
        print(f"\n{'='*70}")
        print(f"  MASTER TRADERS RECORD SUMMARY")
        print(f"{'='*70}")
        print(f"  Traders: {len(traders)} ({', '.join(f'{e}:{c}' for e, c in Counter(t['exchange'] for t in traders).items())})")
        print(f"  Patterns: {len(patterns)} (top WR: {patterns[0]['win_rate']:.1f}% on {patterns[0]['n_trades']} trades)" if patterns else "  Patterns: 0")
        print(f"  Tests queued: {len(test_configs)}")
        print(f"  Sweep cycles: {len(sweep_log)}")
        print(f"\n  Top 10 traders by Sharpe:")
        for t in traders[:10]:
            print(f"    [{t['exchange']:<7}] {t['nickname']:<25} Sharpe={t['sharpe']:>6.2f} ROI={t['roi_30d']:>7.2f}% WR={t['win_rate']:>5.1f}%")
        print(f"\n  Top 3 patterns:")
        for p in patterns[:3]:
            print(f"    WR={p['win_rate']:.1f}% n={p['n_trades']} {p['side']}: {p['rule'][:70]}")
        print(f"\n  V8 test queue:")
        for t in test_configs:
            print(f"    [{t['priority']:<8}] {t['test_id']}: {t['description'][:60]}")
        return
    write_xlsx(traders, patterns, gate_analysis, test_configs, sweep_log)
    write_csv_backup(traders)
    # Save test queue as JSON for V8 sweep to consume
    with open(TEST_QUEUE_JSON, "w") as f:
        json.dump(test_configs, f, indent=2)
    logger.info(f"Test queue: {TEST_QUEUE_JSON}")
    # Also write a summary to 100.md-compatible format
    summary_path = DATA_DIR / "master_traders_summary.md"
    with open(summary_path, "w") as f:
        f.write(f"# Master Traders Record — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write(f"**{len(traders)} traders** across {len(set(t['exchange'] for t in traders))} exchanges\n")
        f.write(f"**{len(patterns)} patterns** discovered | **{len(test_configs)} V8 tests** queued\n\n")
        f.write("## Top Traders by Sharpe\n\n")
        f.write("| Exchange | Name | Sharpe | ROI | WR | DD | Followers |\n")
        f.write("|----------|------|-------:|----:|---:|---:|----------:|\n")
        for t in traders[:15]:
            f.write(f"| {t['exchange']} | {t['nickname']} | {t['sharpe']:.2f} | {t['roi_30d']:.2f}% | {t['win_rate']:.1f}% | {t['drawdown']:.2f}% | {t['followers']} |\n")
        f.write(f"\n*Auto-generated by trader_masters_record.py*\n")
    logger.info(f"Summary: {summary_path}")


def main():
    parser = argparse.ArgumentParser(description="Master Traders Permanent Record")
    parser.add_argument("--inject-tests", action="store_true", help="Generate V8 test configs")
    parser.add_argument("--show", action="store_true", help="Print summary only")
    args = parser.parse_args()
    rebuild_all(inject_tests=args.inject_tests, show_only=args.show)


if __name__ == "__main__":
    main()
