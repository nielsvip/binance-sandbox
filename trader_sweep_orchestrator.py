#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""Master Trader Sweep Orchestrator — 24/7/365 internet-wide trade discovery + V8 reconstruction.

Architecture:
  MacBook + 157.90.168.35  = SCRAPING (internet sweeping)
  204.168.181.211           = PROCESSING (V8 backtesting reconstruction)

Pipeline:
  Phase A — SCRAPE (runs on MacBook/157):
    1. Bitget copy-trading leaderboard → CSV
    2. Binance futures leaderboard → CSV
    3. OKX copy-trading → CSV
    4. Bybit copy-trading → CSV
    5. YouTube strategy scanner → trade links → CSV
    6. Finandy XLSX import (if new file detected)

  Phase B — ANALYZE (runs on MacBook/157):
    7. Merge all exchange CSVs into unified dataset
    8. Deep analysis with indicator overlay (trader_deep_analyzer)
    9. Extract winning patterns + compute trader health

  Phase C — RECONSTRUCT (pushed to 204 server for V8):
    10. Map winning trader entries to NPZ indicators
    11. Run V8 engine on extracted entry/exit signals
    12. Compare V8 results vs trader actual results
    13. Generate actionable config recommendations

  Phase D — REPORT:
    14. Unified research report + email digest
    15. Update tracked channels/traders database

Usage:
  python3 trader_sweep_orchestrator.py                    # Full cycle
  python3 trader_sweep_orchestrator.py --scrape-only      # Phase A only
  python3 trader_sweep_orchestrator.py --analyze-only     # Phase B only (skip scrape)
  python3 trader_sweep_orchestrator.py --daemon           # Run every 4h forever
  python3 trader_sweep_orchestrator.py --daemon --interval 2  # Every 2h
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
DATA_DIR = config.DATA_DIR
REPORT_DIR = DATA_DIR / "trader_research_reports"
MERGED_DIR = DATA_DIR / "trader_sweep_merged"
LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)
MERGED_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("trader_sweep")
logger.setLevel(logging.INFO)
if not logger.handlers:
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console)
    fh = RotatingFileHandler(LOG_DIR / "trader_sweep_orchestrator.log", maxBytes=10_000_000, backupCount=5)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

SHUTDOWN = False
SERVER_204 = "s2-int"
SERVER_157 = "gateway-internal"


def _sigterm(signum, frame):
    global SHUTDOWN
    SHUTDOWN = True
    logger.info("Received signal, shutting down gracefully...")


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)


# ═══════════════════════════════════════════════════════════════════
# PHASE A — SCRAPE ALL EXCHANGES + YOUTUBE
# ═══════════════════════════════════════════════════════════════════

def _run_scraper(name: str, script: str, args: List[str] = None, timeout: int = 900) -> Optional[Path]:
    """Run a scraper subprocess, return output CSV path or None."""
    script_path = BASE_PATH / script
    if not script_path.exists():
        logger.warning(f"[{name}] Script not found: {script_path}")
        return None
    cmd = [PYTHON, str(script_path)] + (args or [])
    logger.info(f"[{name}] Starting: {' '.join(cmd[-3:])}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(BASE_PATH))
        if result.returncode != 0:
            logger.warning(f"[{name}] Exit code {result.returncode}")
            if result.stderr:
                logger.warning(f"[{name}] stderr: {result.stderr[-300:]}")
        # Find the latest CSV in the scraper's data dir
        data_dirs = {
            "bitget": DATA_DIR / "bitget_traders",
            "bybit": DATA_DIR / "bybit_traders",
            "okx": DATA_DIR / "okx_traders",
            "binance": DATA_DIR / "binance_leaderboard",
            "youtube": DATA_DIR / "youtube_strategies",
            "stocks": DATA_DIR / "stock_traders",
        }
        data_dir = data_dirs.get(name.lower().split("_")[0], DATA_DIR)
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        csv_candidates = list(data_dir.glob(f"{today}*trades*.csv")) + list(data_dir.glob("*trades*.csv"))
        if csv_candidates:
            latest = max(csv_candidates, key=lambda p: p.stat().st_mtime)
            lines = sum(1 for _ in open(latest)) - 1
            logger.info(f"[{name}] Output: {latest.name} ({lines} trades)")
            return latest
        logger.info(f"[{name}] No CSV output found")
        return None
    except subprocess.TimeoutExpired:
        logger.error(f"[{name}] Timed out after {timeout}s")
        return None
    except Exception as e:
        logger.error(f"[{name}] Failed: {e}")
        return None


def scrape_bitget() -> Optional[Path]:
    """Scrape Bitget copy-trading traders."""
    return _run_scraper("Bitget", "bitget_trader_scraper.py", ["--full"], timeout=900)


def scrape_binance() -> Optional[Path]:
    """Scrape Binance futures leaderboard: discover traders + snapshot positions + export CSV."""
    script_path = BASE_PATH / "binance_leaderboard_tracker.py"
    if not script_path.exists():
        logger.warning("[Binance] Script not found")
        return None
    logger.info("[Binance] Discovering traders + exporting trades...")
    try:
        # Step 1: Discover fresh traders
        subprocess.run([PYTHON, str(script_path), "--discover"], capture_output=True, text=True, timeout=120, cwd=str(BASE_PATH))
        # Step 2: Snapshot positions (single poll)
        subprocess.run([PYTHON, str(script_path), "--snapshot"], capture_output=True, text=True, timeout=120, cwd=str(BASE_PATH))
        # Step 3: Export completed trades to CSV
        subprocess.run([PYTHON, str(script_path), "--export"], capture_output=True, text=True, timeout=60, cwd=str(BASE_PATH))
        csv_path = DATA_DIR / "binance_leaderboard" / "completed_trades.csv"
        if csv_path.exists() and csv_path.stat().st_size > 100:
            lines = sum(1 for _ in open(csv_path)) - 1
            logger.info(f"[Binance] Exported {lines} completed trades")
            return csv_path
        logger.info("[Binance] No completed trades CSV (may need more monitoring cycles)")
        return None
    except Exception as e:
        logger.error(f"[Binance] Failed: {e}")
        return None


def scrape_okx() -> Optional[Path]:
    """Scrape OKX copy-trading — run one cycle, convert JSONL to CSV."""
    script_path = BASE_PATH / "scripts" / "okx_trader_scraper.py"
    if not script_path.exists():
        logger.warning("[OKX] Script not found")
        return None
    logger.info("[OKX] Running one scrape cycle...")
    try:
        # OKX scraper runs in infinite loop — we import and call run_cycle() directly
        sys.path.insert(0, str(BASE_PATH / "scripts"))
        try:
            import importlib
            okx = importlib.import_module("okx_trader_scraper")
            okx.run_cycle()
        except Exception as e:
            logger.warning(f"[OKX] Direct import failed ({e}), trying subprocess with timeout...")
            result = subprocess.run([PYTHON, str(script_path)], capture_output=True, text=True, timeout=120, cwd=str(BASE_PATH))
        # Convert OKX JSONL to unified CSV
        jsonl_path = DATA_DIR / "okx_traders" / "all_trades.jsonl"
        if jsonl_path.exists():
            return _convert_okx_jsonl_to_csv(jsonl_path)
        return None
    except Exception as e:
        logger.error(f"[OKX] Failed: {e}")
        return None


def _convert_okx_jsonl_to_csv(jsonl_path: Path) -> Optional[Path]:
    """Convert OKX JSONL trades to unified CSV format."""
    trades = []
    seen = set()
    try:
        with open(jsonl_path) as f:
            for line in f:
                try:
                    r = json.loads(line.strip())
                    key = f"{r.get('trader', '')}_{r.get('inst', '')}_{r.get('open_time', '')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    symbol = (r.get("inst", "").replace("-SWAP", "").replace("-", "") or "UNKNOWN")
                    side = r.get("side", "LONG").upper()
                    if side not in ("LONG", "SHORT"):
                        side = "LONG"
                    entry_ts = r.get("open_time", "")
                    exit_ts = r.get("close_time", "")
                    if entry_ts and str(entry_ts).isdigit():
                        ts = int(entry_ts)
                        if ts > 1e12:
                            ts = ts / 1000
                        entry_ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    if exit_ts and str(exit_ts).isdigit():
                        ts = int(exit_ts)
                        if ts > 1e12:
                            ts = ts / 1000
                        exit_ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    trades.append({"trader_id": r.get("uid", r.get("trader", "")), "symbol": symbol, "side": side, "entry_price": float(r.get("entry", 0)), "exit_price": float(r.get("close", 0)), "entry_time": str(entry_ts), "exit_time": str(exit_ts), "pnl": float(r.get("pnl", 0)), "pnl_pct": float(r.get("roe", 0)), "leverage": float(r.get("lever", 1)), "position_size_usd": 0})
                except Exception:
                    continue
    except Exception as e:
        logger.error(f"[OKX] JSONL parse failed: {e}")
        return None
    if not trades:
        return None
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = DATA_DIR / "okx_traders" / f"{today}_trades.csv"
    fieldnames = ["trader_id", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)
    logger.info(f"[OKX] Converted {len(trades)} trades → {csv_path.name}")
    return csv_path


def scrape_bybit() -> Optional[Path]:
    """Scrape Bybit copy-trading."""
    return _run_scraper("Bybit", "bybit_trader_scraper.py", ["--full"], timeout=600)


def scrape_youtube() -> Optional[Path]:
    """Run YouTube strategy scanner."""
    logger.info("[YouTube] Starting strategy scan...")
    script_path = BASE_PATH / "youtube_strategy_scanner.py"
    if not script_path.exists():
        logger.warning("[YouTube] Scanner not found")
        return None
    try:
        result = subprocess.run([PYTHON, str(script_path), "--scan", "--queries", "10"], capture_output=True, text=True, timeout=1800, cwd=str(BASE_PATH))
        # Check for trade links CSV
        yt_dir = DATA_DIR / "youtube_strategies"
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        csv_path = yt_dir / f"trade_links_{today}.csv"
        if csv_path.exists():
            logger.info(f"[YouTube] Found trade links: {csv_path.name}")
            return csv_path
        logger.info("[YouTube] No trade links CSV generated this cycle")
        return None
    except subprocess.TimeoutExpired:
        logger.error("[YouTube] Timed out after 1800s")
        return None
    except Exception as e:
        logger.error(f"[YouTube] Failed: {e}")
        return None


def scrape_stocks() -> Optional[Path]:
    """Run stock trader scanner — congress, insider, 13F, finviz, options flow."""
    logger.info("[Stocks] Starting stock trader scan...")
    script_path = BASE_PATH / "stock_trader_scanner.py"
    if not script_path.exists():
        logger.warning("[Stocks] Scanner not found")
        return None
    try:
        result = subprocess.run([PYTHON, str(script_path), "--full"], capture_output=True, text=True, timeout=1200, cwd=str(BASE_PATH))
        stock_dir = DATA_DIR / "stock_traders"
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        csv_path = stock_dir / f"{today}_trades.csv"
        if csv_path.exists() and csv_path.stat().st_size > 100:
            lines = sum(1 for _ in open(csv_path)) - 1
            logger.info(f"[Stocks] Found {lines} stock trades: {csv_path.name}")
            return csv_path
        merged = stock_dir / "all_stock_trades_merged.csv"
        if merged.exists() and merged.stat().st_size > 100:
            logger.info(f"[Stocks] Using merged file: {merged.name}")
            return merged
        logger.info("[Stocks] No stock trade CSV generated")
        return None
    except subprocess.TimeoutExpired:
        logger.error("[Stocks] Timed out after 1200s")
        return None
    except Exception as e:
        logger.error(f"[Stocks] Failed: {e}")
        return None


def phase_a_scrape() -> Dict[str, Optional[Path]]:
    """Run all scrapers. Returns dict of exchange → CSV path."""
    logger.info("=" * 60)
    logger.info("PHASE A: INTERNET SWEEP — all exchanges + YouTube + Stocks")
    logger.info("=" * 60)
    results = {}
    results["bitget"] = scrape_bitget()
    if SHUTDOWN:
        return results
    results["binance"] = scrape_binance()
    if SHUTDOWN:
        return results
    results["okx"] = scrape_okx()
    if SHUTDOWN:
        return results
    results["bybit"] = scrape_bybit()
    if SHUTDOWN:
        return results
    results["youtube"] = scrape_youtube()
    if SHUTDOWN:
        return results
    results["stocks"] = scrape_stocks()
    successful = sum(1 for v in results.values() if v is not None)
    logger.info(f"PHASE A COMPLETE: {successful}/{len(results)} scrapers produced data")
    return results


# ═══════════════════════════════════════════════════════════════════
# PHASE B — MERGE + DEEP ANALYSIS
# ═══════════════════════════════════════════════════════════════════

def merge_all_exchange_csvs() -> Path:
    """Merge CSVs from ALL exchanges into one unified dataset."""
    logger.info("PHASE B.1: Merging all exchange CSVs...")
    all_rows = []
    seen_keys = set()
    fieldnames = ["trader_id", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd", "exchange"]
    exchange_dirs = {
        "bitget": DATA_DIR / "bitget_traders",
        "bybit": DATA_DIR / "bybit_traders",
        "okx": DATA_DIR / "okx_traders",
        "binance": DATA_DIR / "binance_leaderboard",
        "stocks": DATA_DIR / "stock_traders",
    }
    for exchange, data_dir in exchange_dirs.items():
        if not data_dir.exists():
            continue
        csv_files = sorted(data_dir.glob("*_trades.csv")) + sorted(data_dir.glob("completed_trades.csv"))
        for csv_file in csv_files:
            try:
                with open(csv_file) as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        key = f"{row.get('trader_id', '')}_{row.get('symbol', '')}_{row.get('entry_time', '')}_{row.get('side', '')}"
                        if key not in seen_keys:
                            seen_keys.add(key)
                            row["exchange"] = exchange
                            all_rows.append(row)
            except Exception as e:
                logger.warning(f"Failed to read {csv_file}: {e}")
    merged_path = MERGED_DIR / "all_exchanges_merged.csv"
    if all_rows:
        with open(merged_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)
        logger.info(f"Merged {len(all_rows)} unique trades from {len(exchange_dirs)} exchanges → {merged_path.name}")
    else:
        logger.warning("No trade data found across any exchange")
    return merged_path


def run_deep_analysis(merged_csv: Path) -> Dict[str, Any]:
    """Run trader_deep_analyzer on merged data."""
    logger.info("PHASE B.2: Deep analysis with indicator overlay...")
    try:
        from trader_deep_analyzer import ingest_trades, overlay_indicators, winner_loser_analysis, extract_patterns, compute_trader_health, regime_correlation
    except ImportError as e:
        logger.error(f"Failed to import trader_deep_analyzer: {e}")
        return {}
    if not merged_csv.exists() or merged_csv.stat().st_size < 100:
        logger.error("No merged CSV data")
        return {}
    trades = ingest_trades(str(merged_csv))
    if not trades:
        logger.error("No trades ingested")
        return {}
    logger.info(f"Ingested {len(trades)} trades from {len(set(t.trader_id for t in trades))} traders")
    enriched_count = overlay_indicators(trades)
    logger.info(f"Indicator overlay: {enriched_count}/{len(trades)} enriched ({enriched_count/len(trades)*100:.1f}%)")
    indicator_analysis = winner_loser_analysis(trades)
    patterns = extract_patterns(trades, min_samples_leaf=15)
    health = compute_trader_health(trades)
    regime_data = regime_correlation(trades)
    return {"trades": trades, "enriched_count": enriched_count, "indicator_analysis": indicator_analysis, "patterns": patterns, "health": health, "regime_data": regime_data}


def extract_winning_entries(analysis: Dict) -> List[Dict]:
    """Extract winning trader entries for V8 reconstruction.
    Returns list of {symbol, side, entry_time, indicators_at_entry} for V8 validation."""
    trades = analysis.get("trades", [])
    health = analysis.get("health", {})
    if not trades:
        return []
    # Only use trades from GREEN (healthy) traders with enriched indicator data
    green_ids = {tid for tid, h in health.items() if h.get("status") == "GREEN"}
    winning_entries = []
    for t in trades:
        if not t.is_winner():
            continue
        if t.trader_id not in green_ids:
            continue
        if not t.indicators:
            continue
        entry = {"trader_id": t.trader_id, "symbol": t.symbol, "side": t.side, "entry_time": t.entry_time.isoformat() if hasattr(t.entry_time, "isoformat") else str(t.entry_time), "exit_time": t.exit_time.isoformat() if hasattr(t.exit_time, "isoformat") else str(t.exit_time), "pnl": t.pnl, "pnl_pct": t.pnl_pct, "leverage": t.leverage, "indicators": {k: float(v) for k, v in t.indicators.items() if isinstance(v, (int, float))}}
        winning_entries.append(entry)
    logger.info(f"Extracted {len(winning_entries)} winning entries from GREEN traders for V8 reconstruction")
    return winning_entries


def phase_b_analyze() -> Tuple[Dict, List[Dict]]:
    """Merge + analyze + extract winners."""
    merged = merge_all_exchange_csvs()
    analysis = run_deep_analysis(merged)
    winners = extract_winning_entries(analysis) if analysis else []
    return analysis, winners


# ═══════════════════════════════════════════════════════════════════
# PHASE C — V8 RECONSTRUCTION (pushed to 204 server)
# ═══════════════════════════════════════════════════════════════════

def push_winners_to_server(winners: List[Dict]) -> bool:
    """Push winning entries JSON to 204 server for V8 reconstruction."""
    if not winners:
        logger.info("[V8] No winning entries to push")
        return False
    now = datetime.now(timezone.utc)
    local_path = MERGED_DIR / f"v8_reconstruction_input_{now.strftime('%Y%m%d_%H%M')}.json"
    with open(local_path, "w") as f:
        json.dump({"timestamp": now.isoformat(), "n_entries": len(winners), "entries": winners}, f, indent=2)
    logger.info(f"[V8] Saved {len(winners)} entries to {local_path.name}")
    # SCP to 204 server
    remote_dir = "/home/niels/binance/data/trader_sweep_merged"
    try:
        subprocess.run(["ssh", SERVER_204, f"mkdir -p {remote_dir}"], timeout=10, capture_output=True)
        result = subprocess.run(["scp", str(local_path), f"{SERVER_204}:{remote_dir}/"], timeout=30, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info(f"[V8] Pushed to 204 server: {remote_dir}/{local_path.name}")
            return True
        logger.warning(f"[V8] SCP failed: {result.stderr}")
        return False
    except Exception as e:
        logger.warning(f"[V8] Push to 204 failed: {e}")
        return False


def trigger_v8_reconstruction(winners: List[Dict]) -> Optional[Dict]:
    """Run V8 engine on the 204 server to validate winning trader entries.
    Maps trader entry signals to our NPZ data and checks if our system would have caught them."""
    if not winners:
        return None
    logger.info(f"PHASE C: V8 RECONSTRUCTION — {len(winners)} winning entries")
    # Group entries by symbol for efficient V8 runs
    by_symbol = {}
    for w in winners:
        sym = w["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(w)
    # Build a V8 validation script that checks our indicators at each winning entry time
    validation_results = {"total_entries": len(winners), "symbols_tested": len(by_symbol), "would_have_entered": 0, "missed": 0, "indicator_mismatches": [], "timestamp": datetime.now(timezone.utc).isoformat()}
    # For local execution (if server unavailable), check indicators from NPZ
    npz_dirs = [BASE_PATH / "backtest_v8" / "indicators"]
    if IS_MAC:
        npz_dirs.append(Path("/Users/niels/Documents/binance/backtest_v8/indicators"))
    npz_dir = None
    for d in npz_dirs:
        if d.exists():
            npz_dir = d
            break
    if not npz_dir:
        logger.warning("[V8] No NPZ indicators directory found locally — pushing to server for processing")
        pushed = push_winners_to_server(winners)
        if pushed:
            return _trigger_remote_v8(winners)
        return None
    # Local NPZ reconstruction
    import numpy as np
    for sym, entries in by_symbol.items():
        npz_path = npz_dir / f"{sym}.npz"
        if not npz_path.exists():
            # Try common transformations
            for alt in [sym.replace("USDT", ""), sym + "USDT", sym.replace("-", "")]:
                alt_path = npz_dir / f"{alt}.npz"
                if alt_path.exists():
                    npz_path = alt_path
                    break
        if not npz_path.exists():
            logger.debug(f"[V8] No NPZ for {sym}")
            continue
        try:
            data = np.load(str(npz_path), allow_pickle=True)
            timestamps = data.get("timestamps", np.array([]))
            if len(timestamps) == 0:
                continue
            for entry in entries:
                entry_ts = entry.get("entry_time", "")
                if not entry_ts:
                    continue
                # Parse entry timestamp
                try:
                    if isinstance(entry_ts, str):
                        et = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
                    else:
                        et = datetime.fromtimestamp(float(entry_ts), tz=timezone.utc)
                    target_ts = int(et.timestamp())
                except Exception:
                    continue
                # Find nearest bar
                idx = np.searchsorted(timestamps, target_ts, side="right") - 1
                if idx < 0 or idx >= len(timestamps):
                    continue
                # Check what our indicators said at that moment
                our_indicators = {}
                for key in data.files:
                    if key == "timestamps":
                        continue
                    arr = data[key]
                    if idx < len(arr):
                        val = float(arr[idx])
                        if not np.isnan(val):
                            our_indicators[key] = val
                trader_indicators = entry.get("indicators", {})
                # Compare key indicators
                mismatches = []
                for ind_key in ["stoch_k", "mfi_14", "adx_14", "atr_pct", "rsi_14"]:
                    theirs = trader_indicators.get(ind_key)
                    ours = our_indicators.get(ind_key)
                    if theirs is not None and ours is not None:
                        diff_pct = abs(theirs - ours) / max(abs(theirs), 1) * 100
                        if diff_pct > 20:
                            mismatches.append({"indicator": ind_key, "ours": round(ours, 2), "theirs": round(theirs, 2), "diff_pct": round(diff_pct, 1)})
                # Check if our system would have entered (score-based)
                score = 0
                wt_cross = our_indicators.get("wt_cross_3m", our_indicators.get("wt_cross", 0))
                if wt_cross > 0:
                    score += 8
                stoch_k = our_indicators.get("stoch_k", our_indicators.get("stoch_k_3m", 50))
                if entry["side"] == "LONG" and stoch_k < 50:
                    score += 4
                elif entry["side"] == "SHORT" and stoch_k > 50:
                    score += 4
                mfi = our_indicators.get("mfi_14", our_indicators.get("mfi_4h", 50))
                if entry["side"] == "LONG" and mfi > 50:
                    score += 4
                elif entry["side"] == "SHORT" and mfi < 50:
                    score += 4
                adx = our_indicators.get("adx_14", 25)
                if adx > 25:
                    score += 3
                if score >= 12:
                    validation_results["would_have_entered"] += 1
                else:
                    validation_results["missed"] += 1
                    if mismatches:
                        validation_results["indicator_mismatches"].append({"symbol": sym, "side": entry["side"], "entry_score": score, "mismatches": mismatches})
        except Exception as e:
            logger.warning(f"[V8] NPZ load failed for {sym}: {e}")
    total_checked = validation_results["would_have_entered"] + validation_results["missed"]
    if total_checked > 0:
        hit_rate = validation_results["would_have_entered"] / total_checked * 100
        logger.info(f"[V8] Reconstruction: {validation_results['would_have_entered']}/{total_checked} entries we would have caught ({hit_rate:.1f}%)")
    # Save results
    results_path = MERGED_DIR / f"v8_reconstruction_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"
    with open(results_path, "w") as f:
        json.dump(validation_results, f, indent=2)
    return validation_results


def _trigger_remote_v8(winners: List[Dict]) -> Optional[Dict]:
    """Trigger V8 reconstruction on 204 server via SSH."""
    logger.info("[V8] Triggering remote V8 reconstruction on 204 server...")
    try:
        # Check if server is reachable and not locked
        result = subprocess.run(["ssh", SERVER_204, "test -f /home/niels/SWEEP_RUNNING && echo LOCKED || echo FREE"], capture_output=True, text=True, timeout=10)
        if "LOCKED" in result.stdout:
            logger.warning("[V8] 204 server has SWEEP_RUNNING lock — skipping V8 reconstruction")
            return None
        # Find the latest reconstruction input
        remote_dir = "/home/niels/binance/data/trader_sweep_merged"
        cmd = f"cd /home/niels/binance && ls -t {remote_dir}/v8_reconstruction_input_*.json 2>/dev/null | head -1"
        result = subprocess.run(["ssh", SERVER_204, cmd], capture_output=True, text=True, timeout=10)
        input_file = result.stdout.strip()
        if not input_file:
            logger.warning("[V8] No reconstruction input found on 204 server")
            return None
        logger.info(f"[V8] Remote input: {input_file}")
        return {"status": "pushed_to_204", "input_file": input_file, "n_entries": len(winners)}
    except Exception as e:
        logger.warning(f"[V8] Remote trigger failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# PHASE D — UNIFIED REPORT + EMAIL
# ═══════════════════════════════════════════════════════════════════

def generate_unified_report(scrape_results: Dict, analysis: Dict, winners: List[Dict], v8_results: Optional[Dict]) -> Path:
    """Generate comprehensive research report."""
    logger.info("PHASE D: Generating unified report...")
    now = datetime.now(timezone.utc)
    datestamp = now.strftime("%Y%m%d_%H%M")
    report_path = REPORT_DIR / f"sweep_report_{datestamp}.md"
    trades = analysis.get("trades", [])
    patterns = analysis.get("patterns", [])
    health = analysis.get("health", {})
    indicator_analysis = analysis.get("indicator_analysis", {})
    enriched_count = analysis.get("enriched_count", 0)
    n_traders = len(set(t.trader_id for t in trades)) if trades else 0
    total_pnl = sum(t.pnl for t in trades) if trades else 0
    overall_wr = (sum(1 for t in trades if t.is_winner()) / len(trades) * 100) if trades else 0
    lines = []
    lines.append(f"# Trader Sweep Report — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("## Data Sources")
    for exchange, csv_path in (scrape_results or {}).items():
        status = f"{csv_path.name}" if csv_path else "FAILED/EMPTY"
        lines.append(f"- **{exchange.upper()}**: {status}")
    lines.append("")
    lines.append("## Analysis Summary")
    lines.append(f"- **Trades analyzed**: {len(trades)} ({enriched_count} with indicator overlay)")
    lines.append(f"- **Traders**: {n_traders}")
    lines.append(f"- **Total PnL**: ${total_pnl:,.2f}")
    lines.append(f"- **Overall Win Rate**: {overall_wr:.1f}%")
    lines.append(f"- **Winning entries extracted**: {len(winners)} (GREEN traders only)")
    lines.append(f"- **Patterns found**: {len(patterns)}")
    lines.append("")
    # V8 reconstruction results
    if v8_results:
        lines.append("## V8 Reconstruction")
        total_checked = v8_results.get("would_have_entered", 0) + v8_results.get("missed", 0)
        hit_rate = v8_results["would_have_entered"] / total_checked * 100 if total_checked else 0
        lines.append(f"- **Entries validated**: {total_checked}")
        lines.append(f"- **Our system would have caught**: {v8_results.get('would_have_entered', 0)} ({hit_rate:.1f}%)")
        lines.append(f"- **Missed**: {v8_results.get('missed', 0)}")
        if v8_results.get("indicator_mismatches"):
            lines.append(f"- **Indicator mismatches**: {len(v8_results['indicator_mismatches'])}")
            for mm in v8_results["indicator_mismatches"][:5]:
                lines.append(f"  - {mm['symbol']} {mm['side']}: score={mm['entry_score']}, mismatches={mm['mismatches']}")
        lines.append("")
    # Top indicators
    lines.append("## Top Discriminative Indicators")
    lines.append("")
    if indicator_analysis:
        lines.append("| Indicator | Winner Mean | Loser Mean | Cohen's d | p-value |")
        lines.append("|-----------|------------|------------|----------|---------|")
        for i, (key, vals) in enumerate(indicator_analysis.items()):
            if i >= 10:
                break
            sig = " ***" if vals["p_value"] < 0.001 else " **" if vals["p_value"] < 0.01 else " *" if vals["p_value"] < 0.05 else ""
            lines.append(f"| {key} | {vals['winner_mean']:.4f} | {vals['loser_mean']:.4f} | {vals['cohens_d']:+.4f} | {vals['p_value']:.6f}{sig} |")
    lines.append("")
    # High WR patterns
    lines.append("## High Win-Rate Patterns")
    lines.append("")
    for i, p in enumerate(patterns[:5]):
        if p["win_rate"] >= 0.70:
            lines.append(f"- **WR={p['win_rate']*100:.1f}%** (n={p['n_trades']}) {p['dominant_side']} — `{p['rule_str']}`")
    lines.append("")
    # Trader health
    green = sum(1 for h in health.values() if h.get("status") == "GREEN")
    red = sum(1 for h in health.values() if h.get("status") == "RED")
    yellow = len(health) - green - red
    lines.append(f"## Trader Health: {green} GREEN / {yellow} YELLOW / {red} RED")
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by trader_sweep_orchestrator.py at {now.strftime('%Y-%m-%d %H:%M UTC')}*")
    report_text = "\n".join(lines)
    with open(report_path, "w") as f:
        f.write(report_text)
    logger.info(f"Report written: {report_path}")
    # Also save as JSON
    findings_path = REPORT_DIR / f"sweep_findings_{datestamp}.json"
    with open(findings_path, "w") as f:
        json.dump({"timestamp": now.isoformat(), "n_trades": len(trades), "n_enriched": enriched_count, "n_traders": n_traders, "overall_wr": round(overall_wr, 2), "total_pnl": round(total_pnl, 2), "n_patterns": len(patterns), "n_winners_extracted": len(winners), "v8_results": v8_results, "exchanges_scraped": {k: bool(v) for k, v in (scrape_results or {}).items()}}, f, indent=2)
    return report_path


def email_digest(report_path: Path, analysis: Dict, v8_results: Optional[Dict]):
    """Send findings via email."""
    try:
        from morning_email import send_email
        trades = analysis.get("trades", [])
        patterns = analysis.get("patterns", [])
        n_trades = len(trades) if trades else 0
        wr = (sum(1 for t in trades if t.is_winner()) / n_trades * 100) if n_trades else 0
        html = [f"<h2>Trader Sweep — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}</h2>"]
        html.append(f"<p><b>{n_trades} trades</b> analyzed, <b>{wr:.1f}%</b> WR</p>")
        if v8_results:
            total = v8_results.get("would_have_entered", 0) + v8_results.get("missed", 0)
            if total:
                hit = v8_results["would_have_entered"] / total * 100
                html.append(f"<p>V8: {v8_results['would_have_entered']}/{total} entries caught ({hit:.0f}%)</p>")
        if patterns:
            html.append("<p><b>Top patterns:</b></p><ul>")
            for p in patterns[:5]:
                if p["win_rate"] >= 0.70:
                    html.append(f"<li>WR={p['win_rate']*100:.0f}% (n={p['n_trades']}) {p['dominant_side']}: {p['rule_str'][:80]}</li>")
            html.append("</ul>")
        html.append(f"<p><i>Full report: {report_path.name}</i></p>")
        send_email("\n".join(html), subject=f"Trader Sweep — {n_trades} trades, {len(patterns)} patterns")
        logger.info("Email digest sent")
    except ImportError:
        logger.info("Email not available (morning_email not found)")
    except Exception as e:
        logger.warning(f"Email failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════

def run_full_cycle(scrape_only: bool = False, analyze_only: bool = False):
    """Run the complete sweep cycle."""
    start = time.time()
    logger.info("=" * 70)
    logger.info("TRADER SWEEP ORCHESTRATOR — Starting full cycle")
    logger.info(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"Machine: {'MacBook' if IS_MAC else 'Server'}")
    logger.info("=" * 70)
    scrape_results = None
    if not analyze_only:
        scrape_results = phase_a_scrape()
        if scrape_only:
            logger.info(f"Scrape-only mode — done in {time.time()-start:.0f}s")
            return
    if SHUTDOWN:
        return
    # Phase B
    analysis, winners = phase_b_analyze()
    if not analysis:
        logger.error("Analysis failed — aborting")
        return
    if SHUTDOWN:
        return
    # Phase C — V8 reconstruction
    v8_results = trigger_v8_reconstruction(winners)
    if SHUTDOWN:
        return
    # Phase D — Report
    report_path = generate_unified_report(scrape_results, analysis, winners, v8_results)
    # Use unified newsletter (delta-only) instead of raw email_digest
    try:
        from unified_newsletter import run as send_unified_newsletter
        send_unified_newsletter()
    except Exception as e:
        logger.warning(f"Unified newsletter failed, falling back to legacy: {e}")
        email_digest(report_path, analysis, v8_results)
    # Phase E — Update permanent master record
    try:
        from trader_masters_record import rebuild_all
        rebuild_all()
        logger.info("Master traders record updated (XLSX + CSV + test queue)")
    except Exception as e:
        logger.warning(f"Master record update failed: {e}")
    elapsed = time.time() - start
    logger.info("=" * 70)
    logger.info(f"SWEEP CYCLE COMPLETE in {elapsed:.0f}s")
    logger.info(f"Report: {report_path}")
    logger.info("=" * 70)


def daemon_loop(interval_hours: float = 4.0):
    """Run full cycles forever."""
    logger.info(f"DAEMON MODE: running every {interval_hours}h — will NEVER stop")
    cycle = 0
    while not SHUTDOWN:
        cycle += 1
        logger.info(f"--- Cycle #{cycle} ---")
        try:
            run_full_cycle()
        except Exception as e:
            logger.error(f"Cycle #{cycle} failed: {e}", exc_info=True)
        next_run = datetime.now(timezone.utc) + timedelta(hours=interval_hours)
        logger.info(f"Next run at {next_run.strftime('%Y-%m-%d %H:%M UTC')}")
        wait_until = time.time() + interval_hours * 3600
        while time.time() < wait_until and not SHUTDOWN:
            time.sleep(30)
    logger.info("Daemon shut down cleanly")


def main():
    parser = argparse.ArgumentParser(description="Master Trader Sweep Orchestrator")
    parser.add_argument("--scrape-only", action="store_true", help="Phase A only (scraping)")
    parser.add_argument("--analyze-only", action="store_true", help="Phase B+C+D only (skip scrape)")
    parser.add_argument("--daemon", action="store_true", help="Run forever every N hours")
    parser.add_argument("--interval", type=float, default=4.0, help="Daemon interval in hours (default: 4)")
    parser.add_argument("--report", action="store_true", help="Show latest report")
    args = parser.parse_args()
    if args.report:
        reports = sorted(REPORT_DIR.glob("sweep_report_*.md"), reverse=True)
        if reports:
            print(open(reports[0]).read())
        else:
            print("No sweep reports found.")
        return
    if args.daemon:
        daemon_loop(args.interval)
        return
    run_full_cycle(scrape_only=args.scrape_only, analyze_only=args.analyze_only)


if __name__ == "__main__":
    main()
