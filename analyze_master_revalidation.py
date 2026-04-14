#!/usr/bin/env python3
"""Analyze master revalidation results and update backtest_changes_100.xlsx.

Maps each parameter dimension to specific BACKTEST_CHANGE numbers,
computes the TRUE optimal value across all symbols, and marks
which changes were VALIDATED vs INVALIDATED vs NEEDS_REVISION.

Usage:
  python3 analyze_master_revalidation.py              # Full analysis + xlsx update
  python3 analyze_master_revalidation.py --dry-run    # Show what would change without writing
"""
import json, sys, os
import numpy as np
from pathlib import Path
from collections import defaultdict

BASE_PATH = Path("/Users/niels/Documents/binance")
RESULTS_FILE = BASE_PATH / "data" / "backtest_master" / "results.json"
XLSX_FILE = BASE_PATH / "data" / "backtest_changes_100.xlsx"

# ═══ MAP: parameter dimension → BACKTEST_CHANGE numbers ═══════════════════════
PARAM_TO_CHANGES = {
    "tp": {
        "changes": ["108", "112", "107"],
        "description": "NOLOSS_MIN_PROFIT_PCT / ACCOUNT_TP_PCT / EXIT_GAIN_THRESHOLD",
        "current_value": 0.5,
    },
    "rsi": {
        "changes": ["111", "101"],
        "description": "RSI_ENTRY_GATE (RSI_MAX_LONG / RSI_MIN_SHORT / SHORT_RSI_MIN_1H)",
        "current_value": 37,
    },
    "kz": {
        "changes": ["109"],
        "description": "K_ZONE_ENTRY (K_ZONE_LONG_THRESHOLD / K_ZONE_SHORT_THRESHOLD)",
        "current_value": 35,
    },
    "vol": {
        "changes": ["100"],
        "description": "ENTRY_VOL_MIN_RATIO",
        "current_value": 1.3,
    },
    "atr": {
        "changes": ["103"],
        "description": "ENTRY_ATR_PCT_MIN",
        "current_value": 1.5,
    },
    "ema": {
        "changes": ["3"],
        "description": "EMA_DIST_ENTRY_ENABLED",
        "current_value": 1,
    },
    "mfb": {
        "changes": ["113b"],
        "description": "MOMENTUM_FADE (body_atr_min × vol_min)",
        "current_value": 2.0,
    },
    "dc": {
        "changes": ["122"],
        "description": "DC_EDGE_SIZING_ENABLED",
        "current_value": 1,
    },
    "bn": {
        "changes": ["110"],
        "description": "BOUNCE_REENTRY_ENABLED",
        "current_value": 1,
    },
    "sp": {
        "changes": [],
        "description": "Stochastic period (infrastructure — not a BACKTEST_CHANGE)",
        "current_value": 14,
    },
}

def load_results():
    if not RESULTS_FILE.exists():
        print(f"ERROR: {RESULTS_FILE} not found. Run the master backtest first.")
        sys.exit(1)
    results = json.loads(RESULTS_FILE.read_text())
    print(f"Loaded {len(results):,} results from {RESULTS_FILE}")
    return results

def analyze_parameter(results, param_name, info):
    """For one parameter dimension, compute avg Sharpe/WR/Return per value across ALL results."""
    by_value = defaultdict(lambda: {"sharpe": [], "wr": [], "ret": [], "n_trades": [], "count": 0})
    for r in results:
        val = r.get(param_name)
        if val is None: continue
        by_value[val]["sharpe"].append(r["sh"])
        by_value[val]["wr"].append(r["wr"])
        by_value[val]["ret"].append(r["ret"])
        by_value[val]["n_trades"].append(r["n"])
        by_value[val]["count"] += 1
    if not by_value:
        return None
    rows = []
    for val, stats in sorted(by_value.items(), key=lambda x: x[0] if isinstance(x[0], (int, float)) else 0):
        s = np.array(stats["sharpe"])
        w = np.array(stats["wr"])
        ret = np.array(stats["ret"])
        rows.append({
            "value": val,
            "count": stats["count"],
            "avg_sharpe": round(s.mean(), 2),
            "med_sharpe": round(np.median(s), 2),
            "avg_wr": round(w.mean(), 4),
            "avg_ret": round(ret.mean(), 2),
            "p75_sharpe": round(np.percentile(s, 75), 2),
            "p25_sharpe": round(np.percentile(s, 25), 2),
        })
    # Find best value
    best = max(rows, key=lambda x: x["avg_sharpe"])
    current = info["current_value"]
    current_row = next((r for r in rows if r["value"] == current), None)
    # Determine status
    if current_row and best["value"] == current:
        status = "VALIDATED"
        improvement = 0
    elif current_row:
        improvement = best["avg_sharpe"] - current_row["avg_sharpe"]
        if improvement > 0.5:
            status = "NEEDS_REVISION"
        else:
            status = "VALIDATED"  # Close enough
    else:
        status = "NEEDS_REVISION"
        improvement = 0
    return {
        "param": param_name,
        "description": info["description"],
        "changes": info["changes"],
        "current_value": current,
        "best_value": best["value"],
        "best_avg_sharpe": best["avg_sharpe"],
        "current_avg_sharpe": current_row["avg_sharpe"] if current_row else None,
        "improvement": round(improvement, 2),
        "status": status,
        "rows": rows,
    }

def print_full_report(analyses):
    print(f"\n{'='*130}")
    print("MASTER REVALIDATION — PARAMETER ABLATION REPORT")
    print(f"{'='*130}")
    for a in analyses:
        if a is None: continue
        marker = "✅" if a["status"] == "VALIDATED" else "⚠️"
        print(f"\n{marker} {a['param'].upper()} — {a['description']}")
        print(f"   Changes: {', '.join('BC_'+c for c in a['changes']) if a['changes'] else 'N/A'}")
        print(f"   Current: {a['current_value']} → Best: {a['best_value']} (Δ Sharpe: {a['improvement']:+.2f})")
        print(f"   Status: {a['status']}")
        print(f"   {'Value':<10} {'Count':>8} {'AvgSharpe':>10} {'MedSharpe':>10} {'AvgWR':>8} {'AvgRet':>10} {'P25':>8} {'P75':>8}")
        print(f"   {'-'*74}")
        for r in a["rows"]:
            flag = " ◀ CURRENT" if r["value"] == a["current_value"] else (" ◀ BEST" if r["value"] == a["best_value"] else "")
            print(f"   {str(r['value']):<10} {r['count']:>8} {r['avg_sharpe']:>10.2f} {r['med_sharpe']:>10.2f} {r['avg_wr']:>7.1%} {r['avg_ret']:>10.2f} {r['p25_sharpe']:>8.2f} {r['p75_sharpe']:>8.2f}{flag}")

def update_xlsx(analyses, dry_run=False):
    """Update backtest_changes_100.xlsx with validation status and corrected values."""
    try:
        import openpyxl
    except ImportError:
        print("openpyxl not available — cannot update xlsx")
        return
    if not XLSX_FILE.exists():
        print(f"XLSX not found: {XLSX_FILE}")
        return
    wb = openpyxl.load_workbook(str(XLSX_FILE))
    ws = wb.active
    # Find or add validation columns
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    val_col = None; best_col = None; sharpe_col = None
    for i, h in enumerate(headers):
        if h == "revalidation_status": val_col = i + 1
        if h == "best_value_found": best_col = i + 1
        if h == "revalidation_sharpe": sharpe_col = i + 1
    if val_col is None:
        val_col = ws.max_column + 1; ws.cell(row=1, column=val_col, value="revalidation_status")
    if best_col is None:
        best_col = ws.max_column + 1; ws.cell(row=1, column=best_col, value="best_value_found")
    if sharpe_col is None:
        sharpe_col = ws.max_column + 1; ws.cell(row=1, column=sharpe_col, value="revalidation_sharpe")
    # Build change_id → analysis map
    change_map = {}
    for a in analyses:
        if a is None: continue
        for cid in a["changes"]:
            change_map[cid] = a
    updates = 0
    for row in range(2, ws.max_row + 1):
        change_id = str(ws.cell(row=row, column=1).value or "")
        # Extract number: BACKTEST_CHANGE_108 → 108, BACKTEST_CHANGE_T55 → T55
        cid = change_id.replace("BACKTEST_CHANGE_", "")
        if cid in change_map:
            a = change_map[cid]
            ws.cell(row=row, column=val_col, value=a["status"])
            ws.cell(row=row, column=best_col, value=str(a["best_value"]))
            ws.cell(row=row, column=sharpe_col, value=a["best_avg_sharpe"])
            updates += 1
            if a["status"] == "NEEDS_REVISION":
                # Also update the "new_value" column (column 4) with the best value
                old_val = ws.cell(row=row, column=4).value
                print(f"  REVISION: {change_id}: {old_val} → {a['best_value']} (Sharpe {a['current_avg_sharpe']} → {a['best_avg_sharpe']})")
                if not dry_run:
                    ws.cell(row=row, column=4, value=str(a["best_value"]))
    if not dry_run:
        wb.save(str(XLSX_FILE))
        print(f"\nUpdated {updates} rows in {XLSX_FILE}")
    else:
        print(f"\n[DRY RUN] Would update {updates} rows in {XLSX_FILE}")

def main():
    dry_run = "--dry-run" in sys.argv
    results = load_results()
    # Per-parameter ablation
    analyses = []
    for param_name, info in PARAM_TO_CHANGES.items():
        a = analyze_parameter(results, param_name, info)
        analyses.append(a)
    print_full_report(analyses)
    # Cross-parameter: best overall combo
    print(f"\n{'='*130}")
    print("TOP 20 OVERALL CONFIGS (across all symbols)")
    print(f"{'='*130}")
    # Group by config (all params except symbol)
    by_cfg = defaultdict(lambda: {"sh": [], "wr": [], "ret": []})
    for r in results:
        key = (r["tp"], r["rsi"], r["kz"], r["vol"], r["atr"], r["ema"], r["mfb"], r["dc"], r["bn"], r["sp"], r["dir"])
        by_cfg[key]["sh"].append(r["sh"]); by_cfg[key]["wr"].append(r["wr"]); by_cfg[key]["ret"].append(r["ret"])
    ranked = []
    for key, stats in by_cfg.items():
        n_syms = len(stats["sh"])
        if n_syms < 10: continue  # Need at least 10 symbols to be general
        ranked.append((np.mean(stats["sh"]), key, n_syms, np.mean(stats["wr"]), np.mean(stats["ret"])))
    ranked.sort(reverse=True)
    print(f"{'TP':>4} {'RSI':>4} {'KZ':>3} {'Vol':>4} {'ATR':>4} {'EMA':>4} {'MF':>4} {'DC':>3} {'BN':>3} {'SP':>3} {'Dir':>4} {'#Sym':>5} {'AvgWR':>7} {'AvgRet':>8} {'AvgSharpe':>10}")
    print("-" * 90)
    for sharpe, key, nsym, wr, ret in ranked[:20]:
        tp, rsi_g, kz, vol, atr, ema, mfb, dc, bn, sp, d = key
        print(f"{tp:>4} {rsi_g:>4} {kz:>3} {vol:>4} {atr:>4} {ema:>4} {mfb:>4} {dc:>3} {bn:>3} {sp:>3} {d:>4} {nsym:>5} {wr:>6.1%} {ret:>8.1f} {sharpe:>10.2f}")
    # Update xlsx
    print(f"\n{'='*130}")
    update_xlsx(analyses, dry_run=dry_run)
    # Save full analysis
    analysis_file = BASE_PATH / "data" / "backtest_master" / "analysis.json"
    clean_analyses = []
    for a in analyses:
        if a is None: continue
        clean = {k: v for k, v in a.items() if k != "rows"}
        clean["per_value"] = a["rows"]
        clean_analyses.append(clean)
    analysis_file.write_text(json.dumps(clean_analyses, indent=2))
    print(f"Saved analysis to {analysis_file}")

if __name__ == "__main__":
    main()
