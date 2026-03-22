#!/opt/anaconda3/envs/binance_env/bin/python
"""Prediction Tracker — Snapshots ALL prediction scores every cycle, then measures actual outcomes.

Three components:
  1. SNAPSHOT ENGINE (runs every 3m): Captures all scores for all symbols
  2. OUTCOME EVALUATOR (runs hourly): Checks what actually happened at 3m/15m/30m/1h/4h/D/W
  3. DAILY OPTIMIZER (runs at 4pm ET): Analyzes accuracy, adjusts calculation weights

Data flow:
  rankings.json + market_data_*.json + rating_registry.json
    → snapshots (data/prediction_snapshots/)
    → outcomes (data/prediction_outcomes/)
    → accuracy_report.xlsx (audit_reports/)
    → weight adjustments → config changes

Usage:
  python prediction_tracker.py --daemon         # Run all components
  python prediction_tracker.py --snapshot       # Take one snapshot now
  python prediction_tracker.py --evaluate       # Evaluate pending outcomes now
  python prediction_tracker.py --optimize       # Run daily optimizer now
  python prediction_tracker.py --report         # Generate accuracy report
"""

import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except ImportError:
    ET = timezone(timedelta(hours=-5))

try:
    import openpyxl
except ImportError:
    openpyxl = None

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("prediction_tracker")
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
fh = RotatingFileHandler(str(logs_dir / "prediction_tracker.log"), maxBytes=50 * 1024 * 1024, backupCount=3)
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(fh)

# Target TF map from predictor inventory — tells us WHICH horizon each predictor should be evaluated on
try:
    from predictor_inventory import get_target_tf_map, get_full_inventory
    TARGET_TF_MAP = get_target_tf_map()
except ImportError:
    TARGET_TF_MAP = {}

# Map tracker field names to inventory field names for target TF lookup
FIELD_NAME_MAP = {"rk_final_score_norm": "final_score_norm", "rk_final_score_recent_norm": "final_score_recent_norm", "rk_combined_percentile": "combined_percentile", "rk_order_multiplier": "order_multiplier", "rk_trend_val_norm_lt": "trend_val_norm_lt", "rk_trend_val_norm_st": "trend_val_norm_st", "rk_proximity_score_norm": "proximity_score_norm", "rk_band_score": "band_score", "rk_rel_vol_raw": "rel_vol_raw", "rk_weighted_gains_lt": "weighted_gains_lt", "rk_weighted_gains_st": "weighted_gains_st", "rk_dc_moment": "dc_moment (rankings)", "rk_dc_qty": "dc_qty (rankings)", "rk_dc_width_composite": "dc_width_composite (rankings)", "rk_dc_expansion": "dc_expansion (rankings)", "rk_dc_htf_pos": "dc_htf_pos (rankings)", "rk_dc_ltf_pos": "dc_ltf_pos (rankings)", "rk_lt_rank": "lt_rank", "rk_st_rank": "st_rank", "md_0dc_expansion": "0dc_expansion", "md_0dc_htf_pos": "0dc_htf_pos", "md_0dc_ltf_pos": "0dc_ltf_pos", "md_0dc_moment": "0dc_moment", "md_0dc_qty": "0dc_qty", "md_0dc_width_composite": "0dc_width_composite", "md_0final_score_norm": "0final_score_norm", "md_0market_sentiment_local": "0market_sentiment_local", "md_0market_sentiment_score": "0market_sentiment_score", "md_0ranking_points": "0ranking_points", "md_0ranking_points_global": "0ranking_points_global", "md_0sentiment_rank": "0sentiment_rank", "md_0sentiment_strength": "0sentiment_strength", "reg_net_score": "net_score", "reg_long_score": "long_score", "reg_short_score": "short_score", "stoch_k_3m": "stoch_k_3m", "stoch_k_15m": "stoch_k_15m", "stoch_k_1h": "stoch_k_1h", "rsi_1h": "rsi_1h"}


def _get_target_tf(tracker_field):
    """Look up the target timeframe for a tracker field name."""
    inv_field = FIELD_NAME_MAP.get(tracker_field, tracker_field)
    return TARGET_TF_MAP.get(inv_field, None)

BASE_PATH = Path(__file__).parent
SNAPSHOTS_DIR = BASE_PATH / "data" / "prediction_snapshots"
OUTCOMES_DIR = BASE_PATH / "data" / "prediction_outcomes"
REPORT_DIR = BASE_PATH / "audit_reports"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
OUTCOMES_DIR.mkdir(parents=True, exist_ok=True)

# Evaluation horizons (seconds)
HORIZONS = {
    "3m": 180, "15m": 900, "30m": 1800, "1h": 3600,
    "4h": 14400, "D": 86400, "W": 604800
}

# Score fields to track from each source
RANKING_FIELDS = ["final_score_norm", "final_score_recent_norm", "lt_rank", "st_rank", "combined_percentile", "order_multiplier", "trend_val_norm_lt", "trend_val_norm_st", "proximity_score_norm", "band_score", "rel_vol_raw", "weighted_gains_lt", "weighted_gains_st", "dc_moment", "dc_qty", "dc_width_composite", "dc_expansion", "dc_htf_pos", "dc_ltf_pos"]

MARKET_DATA_FIELDS = ["0dc_expansion", "0dc_htf_pos", "0dc_ltf_pos", "0dc_moment", "0dc_qty", "0dc_width_composite", "0final_score_norm", "0market_sentiment_local", "0market_sentiment_score", "0ranking_points", "0ranking_points_global", "0sentiment_rank", "0sentiment_strength"]

REGISTRY_FIELDS = ["net_score", "long_score", "short_score"]


def sf(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 1: SNAPSHOT ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def take_snapshot():
    """Capture all prediction scores for all symbols right now."""
    ts = datetime.now(timezone.utc)
    ts_str = ts.strftime("%Y%m%d_%H%M%S")
    snapshot = {"timestamp": ts.isoformat(), "symbols": {}}
    # Load rankings
    rk_path = BASE_PATH / "data" / "rankings.json"
    rankings = {}
    if rk_path.exists():
        try:
            rankings = json.load(open(rk_path))
        except Exception:
            pass
    # Load market data
    import glob
    md_files = sorted(glob.glob(str(BASE_PATH / "data" / "market_data_*.json")))
    market_data = {}
    if md_files:
        try:
            market_data = json.load(open(md_files[-1]))
        except Exception:
            pass
    # Load rating registry
    registry = {}
    rr_path = BASE_PATH / "rating_registry.json"
    if rr_path.exists():
        try:
            rr = json.load(open(rr_path))
            for entry in rr.get("unified_ranking", []):
                sym = entry.get("symbol", "")
                if sym:
                    registry[sym] = entry
        except Exception:
            pass
    # Load symbols
    symbols = []
    sym_path = BASE_PATH / "symbols.json"
    if sym_path.exists():
        try:
            symbols = json.load(open(sym_path))
        except Exception:
            pass
    # Also add tradier symbols
    tsym_path = BASE_PATH / "symbols_tradier.json"
    if tsym_path.exists():
        try:
            tsyms = json.load(open(tsym_path))
            symbols = list(set(symbols + tsyms))
        except Exception:
            pass
    for sym in symbols:
        sym_data = {"price": 0.0, "rankings": {}, "market_data": {}, "registry": {}}
        # Rankings
        rk = rankings.get(sym, {})
        for field in RANKING_FIELDS:
            sym_data["rankings"][field] = sf(rk.get(field))
        # Market data
        md = market_data.get(sym, {})
        sym_data["price"] = sf(md.get("current_price"))
        for field in MARKET_DATA_FIELDS:
            sym_data["market_data"][field] = sf(md.get(field))
        # Stoch/indicators for direction prediction
        sym_data["stoch_k_3m"] = sf(md.get("stoch_k_3m"))
        sym_data["stoch_k_15m"] = sf(md.get("stoch_k_15m"))
        sym_data["stoch_k_1h"] = sf(md.get("stoch_k_1h"))
        sym_data["ha_3m"] = str(md.get("ha_3m", ""))
        sym_data["ha_15m"] = str(md.get("ha_15m", ""))
        sym_data["ha_1h"] = str(md.get("ha_1h", ""))
        sym_data["rsi_1h"] = sf(md.get("rsi_1h"))
        # Registry
        reg = registry.get(sym, {})
        for field in REGISTRY_FIELDS:
            sym_data["registry"][field] = sf(reg.get(field))
        snapshot["symbols"][sym] = sym_data
    # Save
    snap_path = SNAPSHOTS_DIR / f"snap_{ts_str}.json"
    with open(snap_path, "w") as f:
        json.dump(snapshot, f)
    logger.info(f"[SNAPSHOT] Captured {len(snapshot['symbols'])} symbols → {snap_path.name}")
    return snap_path


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 2: OUTCOME EVALUATOR
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_outcomes():
    """For each old snapshot, check what actually happened at each horizon."""
    now = time.time()
    now_dt = datetime.now(timezone.utc)
    # Load current prices
    import glob
    md_files = sorted(glob.glob(str(BASE_PATH / "data" / "market_data_*.json")))
    current_prices = {}
    if md_files:
        try:
            md = json.load(open(md_files[-1]))
            for sym, data in md.items():
                current_prices[sym] = sf(data.get("current_price"))
        except Exception:
            pass
    if not current_prices:
        logger.warning("[EVALUATE] No current prices available")
        return
    evaluated = 0
    snap_files = sorted(SNAPSHOTS_DIR.glob("snap_*.json"))
    for snap_path in snap_files:
        try:
            snap = json.load(open(snap_path))
            snap_ts = datetime.fromisoformat(snap["timestamp"])
            age_seconds = (now_dt - snap_ts).total_seconds()
            # Check which horizons we can now evaluate
            outcome_path = OUTCOMES_DIR / snap_path.name.replace("snap_", "outcome_")
            existing_outcome = {}
            if outcome_path.exists():
                try:
                    existing_outcome = json.load(open(outcome_path))
                except Exception:
                    pass
            new_evaluations = False
            for horizon_name, horizon_secs in HORIZONS.items():
                if age_seconds < horizon_secs:
                    continue
                if horizon_name in existing_outcome.get("_evaluated_horizons", []):
                    continue
                # Evaluate this horizon
                if "symbols" not in existing_outcome:
                    existing_outcome["symbols"] = {}
                    existing_outcome["_evaluated_horizons"] = []
                    existing_outcome["snapshot_timestamp"] = snap["timestamp"]
                for sym, snap_data in snap.get("symbols", {}).items():
                    snap_price = sf(snap_data.get("price"))
                    current_price = sf(current_prices.get(sym))
                    if snap_price <= 0 or current_price <= 0:
                        continue
                    pct_change = ((current_price - snap_price) / snap_price) * 100
                    if sym not in existing_outcome["symbols"]:
                        existing_outcome["symbols"][sym] = {}
                    existing_outcome["symbols"][sym][f"return_{horizon_name}"] = round(pct_change, 4)
                    existing_outcome["symbols"][sym][f"price_at_{horizon_name}"] = current_price
                existing_outcome["_evaluated_horizons"].append(horizon_name)
                new_evaluations = True
                evaluated += 1
            if new_evaluations:
                with open(outcome_path, "w") as f:
                    json.dump(existing_outcome, f)
            # Clean up old snapshots (keep 7 days)
            if age_seconds > 7 * 86400:
                snap_path.unlink(missing_ok=True)
                outcome_path.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"[EVALUATE] Error processing {snap_path.name}: {e}")
    if evaluated > 0:
        logger.info(f"[EVALUATE] Evaluated {evaluated} snapshot-horizon combinations")


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 3: ACCURACY REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def generate_accuracy_report():
    """Analyze all outcomes to measure prediction accuracy per score field per horizon."""
    if not openpyxl:
        logger.error("[REPORT] openpyxl not installed")
        return None
    outcome_files = sorted(OUTCOMES_DIR.glob("outcome_*.json"))
    if not outcome_files:
        logger.warning("[REPORT] No outcome files found")
        return None
    # Correlations: for each score field, does higher score predict higher return?
    correlations = defaultdict(lambda: defaultdict(lambda: {"values": [], "returns": []}))
    for outcome_path in outcome_files:
        try:
            outcome = json.load(open(outcome_path))
            snap_ts_str = outcome.get("snapshot_timestamp", "")
            snap_path = SNAPSHOTS_DIR / outcome_path.name.replace("outcome_", "snap_")
            if not snap_path.exists():
                continue
            snap = json.load(open(snap_path))
            for sym, snap_data in snap.get("symbols", {}).items():
                outcome_sym = outcome.get("symbols", {}).get(sym, {})
                if not outcome_sym:
                    continue
                # Collect all score fields
                all_scores = {}
                for field in RANKING_FIELDS:
                    all_scores[f"rk_{field}"] = sf(snap_data.get("rankings", {}).get(field))
                for field in MARKET_DATA_FIELDS:
                    all_scores[f"md_{field}"] = sf(snap_data.get("market_data", {}).get(field))
                for field in REGISTRY_FIELDS:
                    all_scores[f"reg_{field}"] = sf(snap_data.get("registry", {}).get(field))
                all_scores["stoch_k_3m"] = sf(snap_data.get("stoch_k_3m"))
                all_scores["stoch_k_15m"] = sf(snap_data.get("stoch_k_15m"))
                all_scores["stoch_k_1h"] = sf(snap_data.get("stoch_k_1h"))
                all_scores["rsi_1h"] = sf(snap_data.get("rsi_1h"))
                # Pair with returns at each horizon
                for horizon_name in HORIZONS:
                    ret = outcome_sym.get(f"return_{horizon_name}")
                    if ret is None:
                        continue
                    for score_name, score_val in all_scores.items():
                        if score_val != 0:
                            correlations[score_name][horizon_name]["values"].append(score_val)
                            correlations[score_name][horizon_name]["returns"].append(ret)
        except Exception:
            continue
    # Calculate correlations and write to Excel
    wb = openpyxl.Workbook()
    # Sheet 1: Correlation matrix
    ws = wb.active
    ws.title = "Score_Correlations"
    horizons_list = list(HORIZONS.keys())
    ws.append(["Score Field"] + [f"corr_{h}" for h in horizons_list] + [f"n_{h}" for h in horizons_list] + ["avg_corr", "best_horizon"])
    rows = []
    for score_name in sorted(correlations.keys()):
        row = [score_name]
        corrs = []
        for h in horizons_list:
            data = correlations[score_name][h]
            vals = data["values"]
            rets = data["returns"]
            n = len(vals)
            if n < 10:
                row.append(0)
                row.append(n)
                continue
            # Simple correlation: do higher scores predict higher returns?
            import numpy as np
            try:
                corr = float(np.corrcoef(vals, rets)[0, 1])
            except Exception:
                corr = 0
            row.append(round(corr, 4))
            corrs.append(corr)
        for h in horizons_list:
            row.append(len(correlations[score_name][h]["values"]))
        avg_corr = sum(corrs) / len(corrs) if corrs else 0
        best_h = horizons_list[corrs.index(max(corrs))] if corrs else "N/A"
        row.append(round(avg_corr, 4))
        row.append(best_h)
        rows.append((avg_corr, row))
    rows.sort(key=lambda x: -abs(x[0]))
    for _, row in rows:
        ws.append(row)
    # Sheet 2: Top predictors per horizon
    ws2 = wb.create_sheet("Top_Predictors")
    for h in horizons_list:
        ws2.append([f"=== {h} ==="])
        ws2.append(["Score", "Correlation", "N", "Direction"])
        h_scores = []
        for score_name in correlations:
            data = correlations[score_name][h]
            n = len(data["values"])
            if n < 10:
                continue
            import numpy as np
            try:
                corr = float(np.corrcoef(data["values"], data["returns"])[0, 1])
            except Exception:
                corr = 0
            direction = "LONG if high" if corr > 0 else "SHORT if high"
            h_scores.append((abs(corr), score_name, corr, n, direction))
        h_scores.sort(reverse=True)
        for _, name, corr, n, direction in h_scores[:10]:
            ws2.append([name, round(corr, 4), n, direction])
        ws2.append([])
    # Sheet 3: Per-symbol accuracy
    ws3 = wb.create_sheet("Per_Symbol")
    ws3.append(["Symbol", "Snapshots", "Avg_Return_1h", "Avg_Return_4h", "Avg_Return_D", "Best_Score_Predictor"])
    sym_returns = defaultdict(lambda: defaultdict(list))
    for outcome_path in outcome_files:
        try:
            outcome = json.load(open(outcome_path))
            for sym, data in outcome.get("symbols", {}).items():
                for h in horizons_list:
                    ret = data.get(f"return_{h}")
                    if ret is not None:
                        sym_returns[sym][h].append(ret)
        except Exception:
            continue
    for sym in sorted(sym_returns.keys()):
        rets = sym_returns[sym]
        n = max(len(v) for v in rets.values()) if rets else 0
        avg_1h = sum(rets.get("1h", [])) / len(rets["1h"]) if rets.get("1h") else 0
        avg_4h = sum(rets.get("4h", [])) / len(rets["4h"]) if rets.get("4h") else 0
        avg_d = sum(rets.get("D", [])) / len(rets["D"]) if rets.get("D") else 0
        ws3.append([sym, n, round(avg_1h, 4), round(avg_4h, 4), round(avg_d, 4), ""])
    # Sheet 4: TARGET TF ACCURACY — evaluates each predictor ONLY on its intended timeframe
    if TARGET_TF_MAP:
        from openpyxl.styles import PatternFill, Font, Alignment
        YELLOW = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
        RED = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        GREEN = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        BOLD = Font(bold=True)
        ws4 = wb.create_sheet("Target_TF_Accuracy")
        ws4.append(["Predictor", "Target TF", "Corr@Target", "N@Target", "Best Actual TF", "Corr@Best", "Match?", "Gap", "Status", "Action"])
        for col_idx in range(1, 11):
            ws4.cell(row=1, column=col_idx).font = BOLD
        target_rows = []
        for score_name in sorted(correlations.keys()):
            target_tf = _get_target_tf(score_name)
            if not target_tf:
                continue
            target_data = correlations[score_name].get(target_tf, {"values": [], "returns": []})
            target_n = len(target_data["values"])
            target_corr = 0
            if target_n >= 10:
                import numpy as np
                try:
                    target_corr = float(np.corrcoef(target_data["values"], target_data["returns"])[0, 1])
                except Exception:
                    target_corr = 0
            best_h, best_corr, best_n = "N/A", 0, 0
            for h in horizons_list:
                hd = correlations[score_name].get(h, {"values": [], "returns": []})
                hn = len(hd["values"])
                if hn < 10:
                    continue
                import numpy as np
                try:
                    hc = float(np.corrcoef(hd["values"], hd["returns"])[0, 1])
                except Exception:
                    hc = 0
                if abs(hc) > abs(best_corr):
                    best_corr = hc
                    best_h = h
                    best_n = hn
            match = "YES" if best_h == target_tf else "NO"
            gap = round(abs(best_corr) - abs(target_corr), 4) if match == "NO" else 0
            if abs(target_corr) >= 0.1:
                status = "GOOD"
            elif abs(target_corr) >= 0.03:
                status = "WEAK"
            else:
                status = "DEAD"
            if status == "DEAD" and match == "NO" and abs(best_corr) >= 0.05:
                action = f"RETARGET to {best_h} (corr {round(best_corr, 4)})"
            elif status == "DEAD":
                action = "INVESTIGATE — no predictive power on any TF"
            elif status == "WEAK" and match == "NO":
                action = f"Consider retargeting to {best_h} or tuning params"
            else:
                action = "OK — performing on target"
            target_rows.append((status, score_name, target_tf, round(target_corr, 4), target_n, best_h, round(best_corr, 4), match, gap, status, action))
        status_order = {"DEAD": 0, "WEAK": 1, "GOOD": 2}
        target_rows.sort(key=lambda x: (status_order.get(x[0], 3), -abs(x[3])))
        for row_tuple in target_rows:
            _, sn, ttf, tc, tn, bh, bc, m, g, st, act = row_tuple
            ws4.append([sn, ttf, tc, tn, bh, bc, m, g, st, act])
            r = ws4.max_row
            ws4.cell(row=r, column=2).fill = YELLOW
            ws4.cell(row=r, column=2).font = BOLD
            if st == "DEAD":
                ws4.cell(row=r, column=9).fill = RED
            elif st == "GOOD":
                ws4.cell(row=r, column=9).fill = GREEN
            if m == "NO":
                ws4.cell(row=r, column=7).fill = RED
            else:
                ws4.cell(row=r, column=7).fill = GREEN
        ws4.column_dimensions["A"].width = 30
        ws4.column_dimensions["B"].width = 12
        ws4.column_dimensions["C"].width = 12
        ws4.column_dimensions["D"].width = 10
        ws4.column_dimensions["E"].width = 14
        ws4.column_dimensions["F"].width = 12
        ws4.column_dimensions["G"].width = 8
        ws4.column_dimensions["H"].width = 8
        ws4.column_dimensions["I"].width = 10
        ws4.column_dimensions["J"].width = 55
        logger.info(f"[REPORT] Target TF Accuracy sheet: {len(target_rows)} predictors evaluated")
    report_path = REPORT_DIR / f"prediction_accuracy_{datetime.now().strftime('%Y%m%d')}.xlsx"
    wb.save(str(report_path))
    logger.info(f"[REPORT] Written to {report_path}")
    return report_path


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 4: DAILY OPTIMIZER (asks Claude to adjust weights)
# ═══════════════════════════════════════════════════════════════════════════════

def run_daily_optimizer():
    """Read accuracy report and propose weight changes."""
    import subprocess
    report_files = sorted(REPORT_DIR.glob("prediction_accuracy_*.xlsx"), reverse=True)
    if not report_files:
        logger.warning("[OPTIMIZER] No accuracy reports found")
        return
    # Read the latest report for summary
    if not openpyxl:
        return
    wb = openpyxl.load_workbook(str(report_files[0]), read_only=True)
    ws = wb["Score_Correlations"]
    summary_lines = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            summary_lines.append(" | ".join(str(c)[:20] for c in row))
            continue
        if i > 15:
            break
        summary_lines.append(" | ".join(str(c)[:20] for c in row))
    summary = "\n".join(summary_lines)
    # Load Target TF Accuracy sheet if available
    target_tf_summary = ""
    try:
        ws4 = wb["Target_TF_Accuracy"]
        tf_lines = []
        for i, row in enumerate(ws4.iter_rows(values_only=True)):
            if i == 0:
                tf_lines.append(" | ".join(str(c)[:20] for c in row))
                continue
            if i > 40:
                break
            tf_lines.append(" | ".join(str(c)[:20] for c in row))
        if tf_lines:
            target_tf_summary = "\n\nTARGET TIMEFRAME ACCURACY (each predictor has an intended TF it should predict):\n" + "\n".join(tf_lines)
    except Exception:
        pass
    prompt = f"""You are the daily optimizer for a crypto trading system. Analyze prediction score correlations with future returns.

CRITICAL: Each predictor has a TARGET TIMEFRAME it's designed to predict on. Focus your analysis on whether each predictor is performing on its target TF. If a predictor is DEAD on its target TF but alive on another, recommend retargeting or parameter tuning.

CORRELATION MATRIX (all predictors × all horizons):
{summary}
{target_tf_summary}

Rules:
1. For each DEAD predictor (corr < 0.03 on target TF): recommend specific parameter changes or retargeting
2. For each WEAK predictor (0.03-0.10): suggest tuning direction
3. For GOOD predictors (> 0.10): leave alone unless retargeting would improve
4. Focus on the TARGET TF column — that's what matters for each predictor
5. Max 5 actionable changes per run, prioritized by impact

Respond with specific recommendations in format:
CHANGE: <predictor> — <action> — <expected impact>"""

    try:
        result = subprocess.run(["claude", "-p", prompt, "--model", "haiku", "--output-format", "text", "--max-turns", "1"], capture_output=True, text=True, timeout=90, cwd=str(BASE_PATH))
        if result.returncode == 0 and result.stdout.strip():
            analysis = result.stdout.strip()
            # Save to changelog
            changelog = REPORT_DIR / "PREDICTION_OPTIMIZER_CHANGELOG.md"
            entry = f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} — Prediction Optimizer\n\n{analysis}\n\n---\n"
            with open(changelog, "a") as f:
                f.write(entry)
            logger.info(f"[OPTIMIZER] Analysis written to changelog")
            return analysis
    except Exception as e:
        logger.error(f"[OPTIMIZER] Claude call failed: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# DAEMON
# ═══════════════════════════════════════════════════════════════════════════════

async def daemon_loop():
    """Run snapshot every 3min, evaluate every hour, optimize at 4pm ET daily."""
    logger.info("[DAEMON] Prediction Tracker started")
    _last_snapshot = 0
    _last_evaluate = 0
    _last_optimize = ""
    while True:
        try:
            now = time.time()
            # Snapshot every 3 minutes
            if now - _last_snapshot > 180:
                take_snapshot()
                _last_snapshot = now
            # Evaluate every hour
            if now - _last_evaluate > 3600:
                evaluate_outcomes()
                _last_evaluate = now
            # Daily optimizer at 4pm ET
            now_et = datetime.now(ET)
            today_key = now_et.strftime("%Y%m%d")
            if now_et.hour == 16 and now_et.minute < 10 and _last_optimize != today_key:
                logger.info("[DAEMON] Running daily optimization cycle")
                evaluate_outcomes()
                report_path = generate_accuracy_report()
                if report_path:
                    run_daily_optimizer()
                _last_optimize = today_key
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"[DAEMON] Error: {e}", exc_info=True)
            await asyncio.sleep(60)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        asyncio.run(daemon_loop())
    elif "--snapshot" in sys.argv:
        take_snapshot()
    elif "--evaluate" in sys.argv:
        evaluate_outcomes()
    elif "--report" in sys.argv:
        generate_accuracy_report()
    elif "--optimize" in sys.argv:
        evaluate_outcomes()
        generate_accuracy_report()
        run_daily_optimizer()
    else:
        print(__doc__)
