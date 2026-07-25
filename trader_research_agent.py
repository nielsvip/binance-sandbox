#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""Autonomous Trader Research Agent — runs daily via cron.

Pipeline:
1. Scrape fresh trade data from Bitget top traders
2. Run deep analysis with indicator overlay on ALL accumulated CSVs
3. Extract winning patterns via decision trees
4. Compare patterns against our live system gates/thresholds
5. Generate actionable report + email digest

Usage:
  python trader_research_agent.py              # Full daily cycle
  python trader_research_agent.py --scrape     # Scrape only (no analysis)
  python trader_research_agent.py --analyze    # Analyze only (skip scrape)
  python trader_research_agent.py --report     # Show latest report
  python trader_research_agent.py --daemon     # Run every 6h forever
"""
import argparse
import csv
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
IS_MAC = platform.system() == "Darwin"
if IS_MAC:
    BASE_PATH = Path("/Users/niels/Documents/binance")
    PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
    LOG_DIR = Path("/Users/niels/logs")
else:
    BASE_PATH = Path("/home/niels/binance")
    PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"
    LOG_DIR = Path("/home/niels/logs")
sys.path.insert(0, str(BASE_PATH))
from config import Config
config = Config()
DATA_DIR = config.DATA_DIR / "bitget_traders"
ANALYSIS_DIR = config.DATA_DIR / "trader_analysis"
REPORT_DIR = config.DATA_DIR / "trader_research_reports"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("trader_research_agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console)
    try:
        fh = RotatingFileHandler(LOG_DIR / "trader_research_agent.log", maxBytes=5_000_000, backupCount=3)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
    except OSError as exc:
        logger.warning("File logging unavailable: %s", exc)
SHUTDOWN = False


def _sigterm(signum, frame):
    global SHUTDOWN
    SHUTDOWN = True
    logger.info("Received signal, shutting down gracefully...")


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)


# ═══════════════════════════════════════════════════════════════════
# PHASE 1 — SCRAPE FRESH DATA
# ═══════════════════════════════════════════════════════════════════

def scrape_traders() -> Optional[Path]:
    """Run bitget_trader_scraper.py to pull fresh trade data. Returns CSV path or None."""
    logger.info("=== PHASE 1: Scraping fresh Bitget trader data ===")
    script = BASE_PATH / "bitget_trader_scraper.py"
    if not script.exists():
        logger.error(f"Scraper not found: {script}")
        return None
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = DATA_DIR / f"{today}_trades.csv"
    try:
        result = subprocess.run([PYTHON, str(script), "--full"], capture_output=True, text=True, timeout=600, cwd=str(BASE_PATH))
        if result.returncode != 0:
            logger.warning(f"Scraper exited with code {result.returncode}")
            if result.stderr:
                logger.warning(f"Scraper stderr: {result.stderr[-500:]}")
        if csv_path.exists() and csv_path.stat().st_size > 100:
            line_count = sum(1 for _ in open(csv_path)) - 1
            logger.info(f"Scrape complete: {csv_path.name} ({line_count} trades)")
            return csv_path
        logger.warning("Scrape produced no CSV output, will use existing data")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Scraper timed out after 600s")
        return None
    except Exception as e:
        logger.error(f"Scraper failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# PHASE 2 — MERGE ALL CSVs INTO UNIFIED DATASET
# ═══════════════════════════════════════════════════════════════════

def merge_all_csvs() -> Path:
    """Merge all daily CSVs into one master CSV for comprehensive analysis."""
    logger.info("=== PHASE 2: Merging all trade CSVs ===")
    all_rows = []
    seen_keys = set()
    csv_files = sorted(DATA_DIR.glob("*_trades.csv"))
    for csv_file in csv_files:
        try:
            with open(csv_file) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    symbol = str(row.get("symbol", "") or "").strip().upper()
                    position_side = str(
                        row.get("position_side", row.get("side", "")) or ""
                    ).strip().upper()
                    if not symbol or position_side not in ("LONG", "SHORT"):
                        logger.warning(
                            "Rejected merged row with invalid identity: file=%s symbol=%r position_side=%r",
                            csv_file.name,
                            symbol,
                            position_side,
                        )
                        continue
                    row["symbol"] = symbol
                    row["side"] = position_side
                    row["position_side"] = position_side
                    row["entry_order_side"] = (
                        "BUY" if position_side == "LONG" else "SELL"
                    )
                    row["exit_order_side"] = (
                        "SELL" if position_side == "LONG" else "BUY"
                    )
                    key = (
                        f"{row.get('trader_id', '')}_{symbol}_"
                        f"{row.get('entry_time', '')}_{position_side}"
                    )
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_rows.append(row)
        except Exception as e:
            logger.warning(f"Failed to read {csv_file}: {e}")
    merged_path = DATA_DIR / "merged_all_trades.csv"
    if all_rows:
        fieldnames = [
            "trader_id",
            "symbol",
            "side",
            "position_side",
            "entry_order_side",
            "exit_order_side",
            "entry_price",
            "exit_price",
            "entry_time",
            "exit_time",
            "pnl",
            "pnl_pct",
            "leverage",
            "position_size_usd",
        ]
        with open(merged_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)
        logger.info(f"Merged {len(all_rows)} unique trades from {len(csv_files)} CSVs into {merged_path.name}")
    else:
        logger.warning("No trade data found in any CSV")
    return merged_path


# ═══════════════════════════════════════════════════════════════════
# PHASE 3 — DEEP ANALYSIS
# ═══════════════════════════════════════════════════════════════════

def run_deep_analysis(csv_path: Path) -> Dict[str, Any]:
    """Run trader_deep_analyzer on merged data. Returns analysis results dict."""
    logger.info("=== PHASE 3: Running deep analysis with indicator overlay ===")
    try:
        from trader_deep_analyzer import ingest_trades, overlay_indicators, winner_loser_analysis, extract_patterns, compute_trader_health, regime_correlation, generate_report, per_trader_summary
    except ImportError as e:
        logger.error(f"Failed to import trader_deep_analyzer: {e}")
        return {}
    if not csv_path.exists():
        logger.error(f"Merged CSV not found: {csv_path}")
        return {}
    trades = ingest_trades(str(csv_path))
    if not trades:
        logger.error("No trades ingested from merged CSV")
        return {}
    logger.info(f"Ingested {len(trades)} trades from {len(set(t.trader_id for t in trades))} traders")
    logger.info("Starting indicator overlay (this takes ~30s)...")
    enriched_count = overlay_indicators(trades)
    logger.info(f"Indicator overlay: {enriched_count}/{len(trades)} trades enriched ({enriched_count/len(trades)*100:.1f}%)")
    logger.info("Running winner/loser analysis...")
    indicator_analysis = winner_loser_analysis(trades)
    logger.info("Extracting patterns via decision tree...")
    patterns = extract_patterns(trades, min_samples_leaf=15)
    logger.info("Computing trader health scores...")
    health = compute_trader_health(trades)
    logger.info("Computing regime correlation...")
    regime_data = regime_correlation(trades)
    logger.info("Generating standard report files...")
    generate_report(trades, indicator_analysis, patterns, health, regime_data)
    return {"trades": trades, "enriched_count": enriched_count, "indicator_analysis": indicator_analysis, "patterns": patterns, "health": health, "regime_data": regime_data}


# ═══════════════════════════════════════════════════════════════════
# PHASE 3b — CAUSALITY WEIGHTING (opinion_causality_report.json → per-trader weight)
# ═══════════════════════════════════════════════════════════════════

CAUSALITY_REPORT_PATH = config.DATA_DIR / "opinion_causality_report.json"
WEIGHTED_OUTPUT_PATH = config.DATA_DIR / "trader_weighted_conviction.json"
TRADEABLE_KEYS_PATH = BASE_PATH / "tradeable_keys.json"


def _load_causality_weights() -> Dict[str, float]:
    """Build {trader_id: weight} from opinion_causality_report.json.
    weight = clip(max(0, mean_4h_pct × hit_4h_pct/100), 0, 5).
    Default 1.0 for unknown traders (applied at lookup site, not stored).
    Explicit noise_traders → 0.0."""
    if not CAUSALITY_REPORT_PATH.exists():
        logger.warning(f"Causality report not found: {CAUSALITY_REPORT_PATH} — all traders weight=1.0")
        return {}
    try:
        with open(CAUSALITY_REPORT_PATH) as f:
            report = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load causality report: {e}")
        return {}
    weights: Dict[str, float] = {}
    verdict = report.get("verdict", {}) or {}
    for noisy in verdict.get("noise_traders", []) or []:
        tid = noisy.get("trader_id")
        if tid:
            weights[tid] = 0.0
    for entry in report.get("per_trader_top20", []) or []:
        tid = entry.get("trader_id")
        if not tid or tid in weights:
            continue
        h4 = (entry.get("stats_per_horizon", {}) or {}).get("4h", {}) or {}
        mean_pct = float(h4.get("mean_pct", 0.0))
        hit_pct = float(h4.get("hit_rate_pct", 0.0))
        raw = max(0.0, mean_pct * hit_pct / 100.0)
        weights[tid] = max(0.0, min(5.0, raw))
    for entry in report.get("per_trader_bottom10", []) or []:
        tid = entry.get("trader_id")
        if not tid or tid in weights:
            continue
        h4 = (entry.get("stats_per_horizon", {}) or {}).get("4h", {}) or {}
        mean_pct = float(h4.get("mean_pct", 0.0))
        hit_pct = float(h4.get("hit_rate_pct", 0.0))
        raw = max(0.0, mean_pct * hit_pct / 100.0)
        weights[tid] = max(0.0, min(5.0, raw))
    n_zero = sum(1 for w in weights.values() if w == 0.0)
    n_pos = sum(1 for w in weights.values() if w > 0.0)
    logger.info(f"Causality weights loaded: {len(weights)} traders ({n_pos} positive, {n_zero} zero/noise)")
    return weights


_TRADEABLE_USDC_SYMBOLS_CACHE: Optional[set] = None


def _load_usdc_symbol_set() -> set:
    """Return a set of base assets (e.g. {'BTC','ETH'}) that have a USDC variant in tradeable_keys."""
    global _TRADEABLE_USDC_SYMBOLS_CACHE
    if _TRADEABLE_USDC_SYMBOLS_CACHE is not None:
        return _TRADEABLE_USDC_SYMBOLS_CACHE
    bases: set = set()
    if TRADEABLE_KEYS_PATH.exists():
        try:
            with open(TRADEABLE_KEYS_PATH) as f:
                keys = json.load(f)
            for k in keys:
                pair = k.split(":", 1)[1] if ":" in k else k
                if "USDC" in pair:
                    sym = pair.rsplit("_", 1)[0]
                    if sym.endswith("USDC"):
                        bases.add(sym[:-4])
        except Exception as e:
            logger.warning(f"Could not load tradeable_keys.json for USDC mapping: {e}")
    _TRADEABLE_USDC_SYMBOLS_CACHE = bases
    return bases


def _normalize_symbol_to_usdc(symbol: str) -> str:
    """Map BTCUSDC → BTCUSDC if the USDC variant exists in tradeable_keys; else passthrough."""
    if not symbol or not symbol.endswith("USDT"):
        return symbol
    base = symbol[:-4]
    usdc_bases = _load_usdc_symbol_set()
    if base in usdc_bases:
        return base + "USDC"
    return symbol


def build_weighted_conviction(analysis: Dict[str, Any], weights: Dict[str, float]) -> Dict[str, Any]:
    """Aggregate per-symbol conviction weighted by causality.
    For each trade: contribution = pnl_pct × weight, signed by side (LONG=+, SHORT=−).
    Output: {symbol_usdc: {long_score, short_score, net_score, n_trades, contributing_traders}}."""
    trades = analysis.get("trades", []) or []
    if not trades:
        return {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "n_trades_input": 0, "n_traders_with_weight": len(weights), "per_symbol": {}, "top_long": [], "top_short": []}
    per_symbol: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        w = weights.get(t.trader_id, 1.0)
        if w <= 0.0:
            continue
        contribution = float(t.pnl_pct) * w
        sym_usdc = _normalize_symbol_to_usdc(t.symbol)
        if sym_usdc not in per_symbol:
            per_symbol[sym_usdc] = {"raw_symbol": t.symbol, "long_score": 0.0, "short_score": 0.0, "net_score": 0.0, "n_trades": 0, "contributing_traders": set(), "weighted_trade_count": 0.0}
        bucket = per_symbol[sym_usdc]
        if t.side == "LONG":
            bucket["long_score"] += contribution
            bucket["net_score"] += contribution
        elif t.side == "SHORT":
            bucket["short_score"] += contribution
            bucket["net_score"] -= contribution
        bucket["n_trades"] += 1
        bucket["weighted_trade_count"] += w
        bucket["contributing_traders"].add(t.trader_id)
    for sym, bucket in per_symbol.items():
        bucket["contributing_traders"] = sorted(bucket["contributing_traders"])
        bucket["n_contributing_traders"] = len(bucket["contributing_traders"])
        for k in ("long_score", "short_score", "net_score", "weighted_trade_count"):
            bucket[k] = round(bucket[k], 4)
    sorted_long = sorted(per_symbol.items(), key=lambda kv: kv[1]["net_score"], reverse=True)
    sorted_short = sorted(per_symbol.items(), key=lambda kv: kv[1]["net_score"])
    top_long = [{"symbol": s, **{k: v for k, v in b.items() if k != "contributing_traders"}} for s, b in sorted_long[:25] if b["net_score"] > 0]
    top_short = [{"symbol": s, **{k: v for k, v in b.items() if k != "contributing_traders"}} for s, b in sorted_short[:25] if b["net_score"] < 0]
    return {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "causality_report_path": str(CAUSALITY_REPORT_PATH), "n_trades_input": len(trades), "n_trades_used": sum(b["n_trades"] for b in per_symbol.values()), "n_traders_with_weight": len(weights), "n_traders_zero_weight": sum(1 for w in weights.values() if w == 0.0), "per_symbol": per_symbol, "top_long": top_long, "top_short": top_short}


def write_weighted_conviction(analysis: Dict[str, Any]) -> Path:
    """Compute weighted conviction and write to data/trader_weighted_conviction.json."""
    weights = _load_causality_weights()
    payload = build_weighted_conviction(analysis, weights)
    WEIGHTED_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WEIGHTED_OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info(f"Weighted conviction written: {WEIGHTED_OUTPUT_PATH} ({len(payload.get('per_symbol', {}))} symbols, {payload.get('n_trades_used', 0)} trades used)")
    return WEIGHTED_OUTPUT_PATH


# ═══════════════════════════════════════════════════════════════════
# PHASE 4 — COMPARE PATTERNS TO OUR SYSTEM
# ═══════════════════════════════════════════════════════════════════

def _validate_research_trades(trades: List[Any]) -> None:
    """Fail closed when a report population cannot be side/symbol isolated."""
    invalid = []
    for index, trade in enumerate(trades):
        symbol = str(getattr(trade, "symbol", "") or "").strip().upper()
        side = str(
            getattr(trade, "position_side", getattr(trade, "side", "")) or ""
        ).strip().upper()
        account = str(getattr(trade, "trader_id", "") or "").strip()
        if not symbol or side not in ("LONG", "SHORT") or not account:
            invalid.append(
                {
                    "index": index,
                    "source_account": account,
                    "symbol": symbol,
                    "position_side": side,
                }
            )
    if invalid:
        raise ValueError(
            "research report identity contract failed; "
            f"{len(invalid)} invalid trades, sample={invalid[:3]}"
        )


def _pnl_formula_pct(trade: Any) -> Optional[float]:
    entry = float(getattr(trade, "entry_price", 0.0) or 0.0)
    exit_ = float(getattr(trade, "exit_price", 0.0) or 0.0)
    if entry <= 0 or exit_ <= 0:
        return None
    direction = 1.0 if trade.position_side == "LONG" else -1.0
    return direction * (exit_ - entry) / entry * 100.0


def _scope_summary(trades: List[Any]) -> Dict[str, Any]:
    """Return explicit ALL_SYMBOLS and symbol+side normalized summaries."""
    _validate_research_trades(trades)

    def summarize(rows: List[Any]) -> Dict[str, Any]:
        returns = [
            value
            for value in (_pnl_formula_pct(trade) for trade in rows)
            if value is not None
        ]
        raw_pnl = [float(getattr(trade, "pnl", 0.0) or 0.0) for trade in rows]
        return {
            "n_trades": len(rows),
            "n_source_accounts": len({trade.trader_id for trade in rows}),
            "raw_pnl_usd_context_only": round(sum(raw_pnl), 4),
            "equal_weight_mean_return_pct": (
                round(float(sum(returns) / len(returns)), 6) if returns else None
            ),
            "equal_weight_median_return_pct": (
                round(float(np.median(returns)), 6) if returns else None
            ),
            "win_rate_pct": (
                round(100.0 * sum(value > 0 for value in returns) / len(returns), 4)
                if returns
                else None
            ),
            "formula_eligible_trades": len(returns),
        }

    by_side = {
        side: summarize([t for t in trades if t.position_side == side])
        for side in ("LONG", "SHORT")
    }
    by_symbol_side: Dict[str, Any] = {}
    for symbol in sorted({trade.symbol for trade in trades}):
        for side in ("LONG", "SHORT"):
            rows = [
                trade
                for trade in trades
                if trade.symbol == symbol and trade.position_side == side
            ]
            if rows:
                by_symbol_side[f"{symbol}:{side}"] = summarize(rows)
    return {
        "schema_version": 2,
        "source_account_field": "trader_id",
        "symbol_scope": "ALL_SYMBOLS",
        "position_side_scope": ["LONG", "SHORT"],
        "side_isolation": True,
        "raw_dollar_pnl_is_strategy_return": False,
        "all_trades": summarize(trades),
        "by_position_side": by_side,
        "by_symbol_position_side": by_symbol_side,
    }


def _regime_side_summary(trades: List[Any]) -> Dict[str, Dict[str, Any]]:
    """Aggregate normalized per-trade returns by regime and side.

    Raw dollars across unrelated traders, symbols, and sizes are deliberately
    excluded: they are scale-weighted cash outcomes, not a strategy return.
    """
    from trader_deep_analyzer import classify_regime

    buckets: Dict[Tuple[str, str], List[Any]] = {}
    for trade in trades:
        if not trade.indicators:
            continue
        key = (trade.position_side, classify_regime(trade.indicators))
        buckets.setdefault(key, []).append(trade)
    output: Dict[str, Dict[str, Any]] = {}
    for (side, regime), rows in sorted(buckets.items()):
        returns = [
            value
            for value in (_pnl_formula_pct(trade) for trade in rows)
            if value is not None
        ]
        if len(returns) < 20:
            continue
        output[f"ALL_SYMBOLS:{side}:{regime}"] = {
            "symbol_scope": "ALL_SYMBOLS",
            "position_side": side,
            "regime": regime,
            "n_trades": len(rows),
            "n_source_accounts": len({trade.trader_id for trade in rows}),
            "equal_weight_mean_return_pct": round(float(np.mean(returns)), 6),
            "equal_weight_median_return_pct": round(float(np.median(returns)), 6),
            "win_rate_pct": round(
                100.0 * sum(value > 0 for value in returns) / len(returns), 4
            ),
        }
    return output


def compare_to_our_system(analysis: Dict[str, Any]) -> List[str]:
    """Compare extracted patterns and indicator importance against our live system.
    Returns list of actionable findings."""
    logger.info("=== PHASE 4: Comparing findings to our system ===")
    findings = []
    indicator_analysis = analysis.get("indicator_analysis", {})
    patterns = analysis.get("patterns", [])
    health = analysis.get("health", {})
    trades = analysis.get("trades", [])
    if not trades:
        return ["No trade data to analyze"]
    scope = _scope_summary(trades)
    side_summary = scope["by_position_side"]
    findings.append(
        "SCOPE: source_account=trader_id, symbol_scope=ALL_SYMBOLS, "
        "position_side=LONG|SHORT; all side/regime/pattern results are isolated"
    )
    for side in ("LONG", "SHORT"):
        stats = side_summary[side]
        findings.append(
            f"SIDE_SCOPE: ALL_SYMBOLS {side} — n={stats['n_trades']}, "
            f"WR={stats['win_rate_pct']:.1f}%, "
            f"equal-weight mean return={stats['equal_weight_mean_return_pct']:+.3f}% "
            f"(raw PnL ${stats['raw_pnl_usd_context_only']:+,.0f} is context only)"
        )
    # --- 1. Top discriminative indicators ---
    if indicator_analysis:
        top_5 = list(indicator_analysis.items())[:5]
        for name, vals in top_5:
            if vals["p_value"] < 0.05 and abs(vals["cohens_d"]) > 0.2:
                direction = "higher" if vals["cohens_d"] > 0 else "lower"
                findings.append(f"INDICATOR_EDGE: {name} — winners have {direction} values (W={vals['winner_mean']:.3f} vs L={vals['loser_mean']:.3f}, d={vals['cohens_d']:+.3f}, p={vals['p_value']:.4f})")
    # --- 2. High-WR patterns ---
    for p in patterns[:5]:
        if p["win_rate"] >= 0.72 and p["n_trades"] >= 20:
            position_side = p.get("position_side", p.get("dominant_side"))
            if not p.get("side_pure") or position_side not in ("LONG", "SHORT"):
                raise ValueError(
                    f"pooled/ambiguous pattern rejected from report: {p!r}"
                )
            findings.append(
                f"PATTERN: ALL_SYMBOLS {position_side} side-pure "
                f"WR={p['win_rate']*100:.1f}% (n={p['n_trades']}) — "
                f"{p['rule_str']}"
            )
    # --- 3. Regime insights ---
    regime_scopes = _regime_side_summary(trades)
    for stats in sorted(
        regime_scopes.values(), key=lambda value: value["n_trades"], reverse=True
    ):
        findings.append(
            f"REGIME_SCOPE: ALL_SYMBOLS {stats['position_side']} "
            f"{stats['regime']} — n={stats['n_trades']}, "
            f"WR={stats['win_rate_pct']:.1f}%, "
            f"equal-weight mean return={stats['equal_weight_mean_return_pct']:+.3f}%"
        )
    # --- 3b. Overall Sharpe ---
    import numpy as np
    all_pnls = [t.pnl_pct for t in trades if t.pnl_pct != 0]
    if len(all_pnls) > 5:
        sharpe = np.mean(all_pnls) / np.std(all_pnls) if np.std(all_pnls) > 0 else 0  # per-trade pool_sharpe (sqrt(252) stripped 2026-04-29 per CLAUDE.md rule 4)
        findings.append(f"pool_sharpe: {sharpe:.4f} (per-trade, {len(all_pnls)} trades)")
    # --- 4. Healthy trader edge ---
    green_traders = {tid: h for tid, h in health.items() if h["status"] == "GREEN" and h["n_trades"] >= 20}
    if green_traders:
        green_trades = [t for t in trades if t.trader_id in green_traders and t.indicators]
        if green_trades:
            green_winners = [t for t in green_trades if t.is_winner()]
            green_wr = len(green_winners) / len(green_trades) * 100 if green_trades else 0
            findings.append(f"GREEN_TRADERS: {len(green_traders)} healthy traders, {len(green_trades)} enriched trades, WR={green_wr:.1f}%")
            # What indicators do GREEN trader winners share?
            if len(green_winners) >= 10:
                import numpy as np
                stoch_vals = [t.indicators.get("stoch_k") for t in green_winners if t.indicators.get("stoch_k") is not None]
                rsi_vals = [t.indicators.get("rsi_14") for t in green_winners if t.indicators.get("rsi_14") is not None]
                atr_vals = [t.indicators.get("atr_pct") for t in green_winners if t.indicators.get("atr_pct") is not None]
                if stoch_vals:
                    findings.append(f"GREEN_ENTRY_STOCH: median K={np.median(stoch_vals):.1f}, mean={np.mean(stoch_vals):.1f} (at entry)")
                if rsi_vals:
                    findings.append(f"GREEN_ENTRY_RSI: median={np.median(rsi_vals):.1f}, mean={np.mean(rsi_vals):.1f} (at entry)")
                if atr_vals:
                    findings.append(f"GREEN_ENTRY_ATR%: median={np.median(atr_vals):.2f}%, mean={np.mean(atr_vals):.2f}%")
    # --- 5. Hold time analysis ---
    all_winners = [t for t in trades if t.is_winner() and t.hold_hours() > 0]
    all_losers = [t for t in trades if not t.is_winner() and t.hold_hours() > 0]
    if all_winners and all_losers:
        import numpy as np
        w_hold = np.median([t.hold_hours() for t in all_winners])
        l_hold = np.median([t.hold_hours() for t in all_losers])
        findings.append(f"HOLD_TIME: winner median={w_hold:.1f}h, loser median={l_hold:.1f}h")
    # --- 7. Leverage sweet spot ---
    import numpy as np
    leveraged_winners = [t for t in trades if t.is_winner() and t.leverage > 0]
    leveraged_losers = [t for t in trades if not t.is_winner() and t.leverage > 0]
    if leveraged_winners and leveraged_losers:
        w_lev = np.median([t.leverage for t in leveraged_winners])
        l_lev = np.median([t.leverage for t in leveraged_losers])
        findings.append(f"LEVERAGE: winner median={w_lev:.1f}x, loser median={l_lev:.1f}x")
    return findings


# ═══════════════════════════════════════════════════════════════════
# PHASE 5 — GENERATE RESEARCH REPORT
# ═══════════════════════════════════════════════════════════════════

def generate_research_report(analysis: Dict[str, Any], findings: List[str]) -> Path:
    """Generate a comprehensive research report with actionable recommendations."""
    logger.info("=== PHASE 5: Generating research report ===")
    now = datetime.now(timezone.utc)
    datestamp = now.strftime("%Y%m%d_%H%M")
    report_path = REPORT_DIR / f"research_{datestamp}.md"
    trades = analysis.get("trades", [])
    scope_summary = _scope_summary(trades) if trades else {
        "schema_version": 2,
        "symbol_scope": "ALL_SYMBOLS",
        "position_side_scope": ["LONG", "SHORT"],
        "side_isolation": True,
        "all_trades": {},
        "by_position_side": {},
        "by_symbol_position_side": {},
    }
    regime_side_summary = _regime_side_summary(trades) if trades else {}
    patterns = analysis.get("patterns", [])
    health = analysis.get("health", {})
    indicator_analysis = analysis.get("indicator_analysis", {})
    enriched_count = analysis.get("enriched_count", 0)
    n_traders = len(set(t.trader_id for t in trades)) if trades else 0
    total_pnl = sum(t.pnl for t in trades) if trades else 0
    overall_wr = (sum(1 for t in trades if t.is_winner()) / len(trades) * 100) if trades else 0
    lines = []
    lines.append(f"# Trader Research Report — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- **Trades analyzed**: {len(trades)} ({enriched_count} with indicator overlay)")
    lines.append(f"- **Source accounts (`trader_id`)**: {n_traders}")
    lines.append("- **Symbol scope**: ALL_SYMBOLS (per-symbol breakdown below)")
    lines.append("- **Position sides**: LONG and SHORT, computed independently")
    lines.append(
        f"- **Raw source PnL (context only)**: ${total_pnl:,.2f} "
        "(not normalized; not a strategy return)"
    )
    if scope_summary["all_trades"]:
        lines.append(
            "- **Equal-weight mean price return/trade**: "
            f"{scope_summary['all_trades']['equal_weight_mean_return_pct']:+.3f}%"
        )
    lines.append(f"- **Overall Win Rate**: {overall_wr:.1f}%")
    lines.append(f"- **Patterns found**: {len(patterns)} (>70% WR, min 15 trades)")
    lines.append("")
    lines.append("## Side-Isolated Population")
    lines.append("")
    lines.append(
        "| Symbol scope | Position side | Trades | Accounts | Win rate | "
        "Mean return/trade | Median return/trade | Raw PnL (context only) |"
    )
    lines.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    )
    for side in ("LONG", "SHORT"):
        stats = scope_summary["by_position_side"].get(side)
        if not stats:
            continue
        lines.append(
            f"| ALL_SYMBOLS | {side} | {stats['n_trades']} | "
            f"{stats['n_source_accounts']} | {stats['win_rate_pct']:.1f}% | "
            f"{stats['equal_weight_mean_return_pct']:+.3f}% | "
            f"{stats['equal_weight_median_return_pct']:+.3f}% | "
            f"${stats['raw_pnl_usd_context_only']:+,.2f} |"
        )
    lines.append("")
    lines.append(
        "`Raw PnL` is the sum of unrelated source-account cash outcomes across "
        "different symbols, sizes, and leverage. It is shown only for source "
        "reconciliation and must not be interpreted as portfolio performance."
    )
    lines.append("")
    lines.append("## Actionable Findings")
    lines.append("")
    if findings:
        for f in findings:
            lines.append(f"- {f}")
    else:
        lines.append("- No actionable findings this cycle.")
    lines.append("")
    lines.append("## Top Discriminative Indicators (Winners vs Losers)")
    lines.append("")
    if indicator_analysis:
        lines.append("| Indicator | Winner Mean | Loser Mean | Cohen's d | p-value |")
        lines.append("|-----------|------------|------------|----------|---------|")
        for i, (key, vals) in enumerate(indicator_analysis.items()):
            if i >= 15:
                break
            sig = " ***" if vals["p_value"] < 0.001 else " **" if vals["p_value"] < 0.01 else " *" if vals["p_value"] < 0.05 else ""
            lines.append(f"| {key} | {vals['winner_mean']:.4f} | {vals['loser_mean']:.4f} | {vals['cohens_d']:+.4f} | {vals['p_value']:.6f}{sig} |")
    else:
        lines.append("No indicator data available.")
    lines.append("")
    lines.append("## High Win-Rate Patterns")
    lines.append("")
    if patterns:
        for i, p in enumerate(patterns[:10]):
            lines.append(f"### Pattern #{i+1}: WR={p['win_rate']*100:.1f}% (n={p['n_trades']})")
            lines.append(
                f"- Scope: ALL_SYMBOLS | Position side: "
                f"{p.get('position_side', p.get('dominant_side'))} | "
                f"Side-pure: {bool(p.get('side_pure'))} | "
                f"Avg PnL%: {p['avg_pnl_pct']:.2f}%"
            )
            lines.append(f"- Rule: `{p['rule_str']}`")
            lines.append("")
    else:
        lines.append("No patterns with >70% WR and sufficient sample size.")
    lines.append("")
    lines.append("## Regime Results by Position Side")
    lines.append("")
    lines.append(
        "| Symbol scope | Position side | Regime | Trades | Accounts | "
        "Win rate | Mean return/trade | Median return/trade |"
    )
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|")
    for stats in sorted(
        regime_side_summary.values(),
        key=lambda value: (value["position_side"], -value["n_trades"], value["regime"]),
    ):
        lines.append(
            f"| ALL_SYMBOLS | {stats['position_side']} | {stats['regime']} | "
            f"{stats['n_trades']} | {stats['n_source_accounts']} | "
            f"{stats['win_rate_pct']:.1f}% | "
            f"{stats['equal_weight_mean_return_pct']:+.3f}% | "
            f"{stats['equal_weight_median_return_pct']:+.3f}% |"
        )
    lines.append("")
    lines.append("## Per-Symbol + Position-Side Results")
    lines.append("")
    lines.append(
        "| Symbol | Position side | Trades | Accounts | Win rate | "
        "Mean return/trade | Raw PnL (context only) |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    symbol_side_rows = sorted(
        scope_summary["by_symbol_position_side"].items(),
        key=lambda item: (-item[1]["n_trades"], item[0]),
    )
    for key, stats in symbol_side_rows:
        symbol, side = key.rsplit(":", 1)
        lines.append(
            f"| {symbol} | {side} | {stats['n_trades']} | "
            f"{stats['n_source_accounts']} | {stats['win_rate_pct']:.1f}% | "
            f"{stats['equal_weight_mean_return_pct']:+.3f}% | "
            f"${stats['raw_pnl_usd_context_only']:+,.2f} |"
        )
    lines.append("")
    lines.append("## Trader Health")
    lines.append("")
    red_traders = {tid: h for tid, h in health.items() if h["status"] == "RED"}
    green_traders = {tid: h for tid, h in health.items() if h["status"] == "GREEN"}
    lines.append(f"- **GREEN**: {len(green_traders)} traders (healthy)")
    lines.append(f"- **YELLOW**: {len(health) - len(green_traders) - len(red_traders)} traders (caution)")
    lines.append(f"- **RED**: {len(red_traders)} traders (blowup risk)")
    if red_traders:
        lines.append("")
        lines.append("### RED Traders (avoid copying)")
        for tid, h in red_traders.items():
            lines.append(f"- {tid[:12]}.. Score={h['score']}/100 | WR={h['win_rate']:.1f}% | PnL=${h['total_pnl']:.2f}")
            for w in h["warnings"][:3]:
                lines.append(f"  - {w}")
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by trader_research_agent.py at {now.strftime('%Y-%m-%d %H:%M UTC')}*")
    report_text = "\n".join(lines)
    with open(report_path, "w") as f:
        f.write(report_text)
    logger.info(f"Research report written: {report_path}")
    # Also save findings as JSON for programmatic access
    findings_path = REPORT_DIR / f"findings_{datestamp}.json"
    with open(findings_path, "w") as f:
        json.dump(
            {
                "schema_version": 2,
                "timestamp": now.isoformat(),
                "scope": scope_summary,
                "n_trades": len(trades),
                "n_enriched": enriched_count,
                "n_traders": n_traders,
                "overall_wr": round(overall_wr, 2),
                "raw_total_pnl_usd_context_only": round(total_pnl, 2),
                "raw_total_pnl_is_strategy_return": False,
                "n_patterns": len(patterns),
                "findings": findings,
                "regime_by_position_side": regime_side_summary,
                "top_patterns": [
                    {
                        "rule": p["rule_str"],
                        "wr": p["win_rate"],
                        "n": p["n_trades"],
                        "symbol_scope": p.get("symbol_scope", "ALL_SYMBOLS"),
                        "position_side": p.get(
                            "position_side", p.get("dominant_side")
                        ),
                        "side_pure": bool(p.get("side_pure")),
                        "avg_pnl_pct": p["avg_pnl_pct"],
                    }
                    for p in patterns[:10]
                ],
            },
            f,
            indent=2,
        )
    logger.info(f"Findings JSON written: {findings_path}")
    return report_path


# ═══════════════════════════════════════════════════════════════════
# PHASE 6 — EMAIL DIGEST (optional)
# ═══════════════════════════════════════════════════════════════════

def email_digest(report_path: Path, findings: List[str]):
    """Send findings as email digest if morning_email infrastructure is available."""
    try:
        from morning_email import send_email
        lines = [f"<h2>Trader Research — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}</h2>", f"<p><b>{len(findings)} findings</b></p>", "<ul>"]
        # Scope and side isolation are safety-critical provenance.  Do not
        # silently truncate them behind a list of pooled findings.
        for f in findings[:50]:
            lines.append(f"<li>{f}</li>")
        lines.append("</ul>")
        lines.append(f"<p><i>Full report: {report_path.name}</i></p>")
        send_email("\n".join(lines), subject=f"Trader Research — {len(findings)} findings")
        logger.info("Email digest sent")
    except ImportError:
        logger.info("Email not available (morning_email not found), skipping")
    except Exception as e:
        logger.warning(f"Email failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════

def run_full_cycle(skip_scrape: bool = False):
    """Run the complete research cycle."""
    start = time.time()
    logger.info(f"{'='*60}")
    logger.info(f"TRADER RESEARCH AGENT — Starting full cycle")
    logger.info(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"{'='*60}")
    # Phase 1: Scrape
    if not skip_scrape:
        scrape_traders()
    else:
        logger.info("Skipping scrape (--analyze mode)")
    # Phase 2: Merge
    merged_csv = merge_all_csvs()
    if not merged_csv.exists() or merged_csv.stat().st_size < 100:
        logger.error("No merged data available. Aborting.")
        return
    # Phase 3: Deep analysis
    analysis = run_deep_analysis(merged_csv)
    if not analysis:
        logger.error("Deep analysis failed. Aborting.")
        return
    try:
        write_weighted_conviction(analysis)
    except Exception as e:
        logger.warning(f"Weighted conviction write failed: {e}")
    # Phase 4: Compare to our system
    findings = compare_to_our_system(analysis)
    # Phase 5: Generate report
    report_path = generate_research_report(analysis, findings)
    # Phase 6: Email — use unified newsletter (delta-only)
    try:
        from unified_newsletter import run as send_unified_newsletter
        send_unified_newsletter()
    except Exception as e:
        logger.warning(f"Unified newsletter failed, falling back to legacy: {e}")
        email_digest(report_path, findings)
    elapsed = time.time() - start
    logger.info(f"{'='*60}")
    logger.info(f"CYCLE COMPLETE in {elapsed:.0f}s — {len(findings)} findings")
    logger.info(f"Report: {report_path}")
    logger.info(f"{'='*60}")
    # Print findings to stdout
    print(f"\n{'='*70}")
    print(f"  TRADER RESEARCH FINDINGS ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})")
    print(f"{'='*70}")
    for f in findings:
        print(f"  • {f}")
    if not findings:
        print("  No actionable findings this cycle.")
    print(f"{'='*70}\n")


def show_latest_report():
    """Display the most recent research report."""
    reports = sorted(REPORT_DIR.glob("research_*.md"), reverse=True)
    if not reports:
        print("No research reports found. Run a full cycle first.")
        return
    with open(reports[0]) as f:
        print(f.read())


def daemon_loop(interval_hours: float = 6.0):
    """Run full cycles on a loop."""
    logger.info(f"Daemon mode: running every {interval_hours}h")
    while not SHUTDOWN:
        try:
            run_full_cycle()
        except Exception as e:
            logger.error(f"Cycle failed: {e}", exc_info=True)
        next_run = datetime.now(timezone.utc) + timedelta(hours=interval_hours)
        logger.info(f"Next run at {next_run.strftime('%Y-%m-%d %H:%M UTC')}")
        wait_until = time.time() + interval_hours * 3600
        while time.time() < wait_until and not SHUTDOWN:
            time.sleep(30)
    logger.info("Daemon shut down cleanly")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Trader Research Agent")
    parser.add_argument("--scrape", action="store_true", help="Scrape only (no analysis)")
    parser.add_argument("--analyze", action="store_true", help="Analyze only (skip scrape)")
    parser.add_argument("--report", action="store_true", help="Show latest research report")
    parser.add_argument("--daemon", action="store_true", help="Run every 6h forever")
    parser.add_argument("--interval", type=float, default=6.0, help="Daemon interval in hours (default: 6)")
    args = parser.parse_args()
    if args.report:
        show_latest_report()
        return
    if args.scrape:
        scrape_traders()
        return
    if args.daemon:
        daemon_loop(args.interval)
        return
    run_full_cycle(skip_scrape=args.analyze)


if __name__ == "__main__":
    main()
