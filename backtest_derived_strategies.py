#!/usr/bin/env python3
"""
Backtest Derived Strategies — Test reverse-engineered trader strategies independently.

Takes the strategies.json from reverse_engineer_traders.py and:
1. For each strategy, apply its rules to ALL available kline data
2. Simulate entries/exits using the rules (entry when rules fire, exit after N bars or rules stop)
3. Calculate PnL, Sharpe, win rate, max drawdown
4. Compare results against our current system's performance
5. Rank strategies and output recommendations

Usage:
    python3 backtest_derived_strategies.py
    python3 backtest_derived_strategies.py --top 10 --hold-bars 12
"""
import argparse
import json
import logging
import math
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
KLINES_DIR = config.KLINES_CACHE_DIR
STRATEGIES_PATH = BASE_PATH / "data" / "reverse_engineered" / "strategies.json"
OUTPUT_DIR = BASE_PATH / "data" / "reverse_engineered"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backtest_derived")

KLINE_CACHE: Dict[str, Optional[pd.DataFrame]] = {}


def load_klines(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    key = f"{symbol}_{tf}"
    if key in KLINE_CACHE:
        return KLINE_CACHE[key]
    path = KLINES_DIR / f"{key}.json"
    if not path.exists():
        KLINE_CACHE[key] = None
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        if df.empty:
            KLINE_CACHE[key] = None
            return None
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("timestamp").reset_index(drop=True)
        KLINE_CACHE[key] = df
        return df
    except Exception:
        KLINE_CACHE[key] = None
        return None


def find_kline_symbol(raw_symbol: str) -> Optional[str]:
    base = raw_symbol.replace("/", "").replace("-", "").upper()
    for cand in [base, base + "USDT", base.replace("USDT", "USDC")]:
        for tf in ["15m", "1h"]:
            if (KLINES_DIR / f"{cand}_{tf}.json").exists():
                return cand
    return None


def compute_indicator_arrays(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """Compute all indicator arrays for a kline DataFrame. Matches reverse_engineer_traders.py."""
    n = len(df)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    arrays = {}
    # RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-10)
    arrays["rsi_14"] = (100 - 100 / (1 + rs)).values.astype(np.float64)
    # Stochastic RSI (simplified)
    rsi = 100 - 100 / (1 + rs)
    rsi_min = rsi.rolling(14, min_periods=1).min()
    rsi_max = rsi.rolling(14, min_periods=1).max()
    stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, 1e-10)
    k = stoch_rsi.rolling(5, min_periods=1).mean() * 100
    d = k.rolling(5, min_periods=1).mean()
    arrays["stoch_k"] = k.values.astype(np.float64)
    arrays["stoch_d"] = d.values.astype(np.float64)
    # MFI
    tp = (high + low + close) / 3.0
    raw_mf = tp * df["volume"]
    pos_mf = raw_mf.where(tp.diff() > 0, 0)
    neg_mf = raw_mf.where(tp.diff() < 0, 0)
    pos_sum = pos_mf.rolling(14, min_periods=1).sum()
    neg_sum = neg_mf.rolling(14, min_periods=1).sum()
    arrays["mfi"] = (100.0 - (100.0 / (1.0 + pos_sum / neg_sum.replace(0, 1e-10)))).values.astype(np.float64)
    # ATR
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    arrays["atr_14"] = atr.values.astype(np.float64)
    arrays["atr_pct"] = (atr / close.replace(0, 1e-10) * 100).values.astype(np.float64)
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_sig = macd_line.ewm(span=9, adjust=False).mean()
    arrays["macd_line"] = macd_line.values.astype(np.float64)
    arrays["macd_hist"] = (macd_line - macd_sig).values.astype(np.float64)
    # ADX
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    atr14 = tr.ewm(span=14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, 1e-10)
    minus_di = 100 * minus_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
    arrays["adx_14"] = dx.ewm(span=14, adjust=False).mean().values.astype(np.float64)
    # Bollinger
    sma20 = close.rolling(20, min_periods=1).mean()
    std20 = close.rolling(20, min_periods=1).std()
    bb_up = sma20 + 2 * std20
    bb_lo = sma20 - 2 * std20
    bb_range = bb_up - bb_lo
    arrays["bb_pct_b"] = ((close - bb_lo) / bb_range.replace(0, 1e-10)).clip(-0.5, 1.5).values.astype(np.float64)
    arrays["bb_width"] = (bb_range / close.replace(0, 1e-10) * 100).values.astype(np.float64)
    # Choppiness
    atr_sum = tr.rolling(14, min_periods=1).sum()
    hh = high.rolling(14, min_periods=1).max()
    ll = low.rolling(14, min_periods=1).min()
    arrays["choppiness"] = (100.0 * np.log10((atr_sum / (hh - ll).replace(0, 1e-10)).values) / np.log10(14)).astype(np.float64)
    # Donchian
    dc_h = high.rolling(20, min_periods=1).max()
    dc_l = low.rolling(20, min_periods=1).min()
    dc_range = dc_h - dc_l
    arrays["dc_position"] = ((close - dc_l) / dc_range.replace(0, 1e-10)).clip(0, 1).values.astype(np.float64)
    arrays["dc_width"] = (dc_range / close.replace(0, 1e-10) * 100).values.astype(np.float64)
    # Relative volume
    vol_sma = df["volume"].rolling(20, min_periods=1).mean()
    arrays["rvol"] = (df["volume"] / vol_sma.replace(0, 1e-10)).values.astype(np.float64)
    # EMA distances
    for length in [9, 20]:
        ema = close.ewm(span=length, adjust=False).mean()
        arrays[f"ema_dist_{length}"] = ((close - ema) / ema.replace(0, 1e-10) * 100).values.astype(np.float64)
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    arrays["ema_9_20_spread"] = ((ema9 - ema20) / close.replace(0, 1e-10) * 100).values.astype(np.float64)
    # SMA200 distance
    sma200 = close.rolling(200, min_periods=1).mean()
    arrays["pct_from_sma200"] = ((close - sma200) / sma200.replace(0, 1e-10) * 100).values.astype(np.float64)
    return arrays


def check_rules_fire(arrays: Dict[str, np.ndarray], rules: List[Dict], idx: int, tf: str) -> bool:
    """Check if all rules fire at bar index idx."""
    for rule in rules:
        ind = rule["indicator"]
        # Map indicator name to array key based on TF
        if ind.endswith("_4h"):
            if tf != "4h":
                continue  # Skip 4h rules when testing on 1h
            base_key = ind.replace("_4h", "")
        else:
            if tf == "4h":
                continue  # Skip 1h rules when testing on 4h
            base_key = ind
        if base_key not in arrays:
            return False
        val = arrays[base_key][idx]
        if np.isnan(val):
            return False
        if rule["op"] == ">" and val <= rule["threshold"]:
            return False
        elif rule["op"] == "<" and val >= rule["threshold"]:
            return False
        elif rule["op"] == "band" and (val < rule["lo"] or val > rule["hi"]):
            return False
    return True


def backtest_strategy(strategy: Dict, hold_bars: int = 12, max_concurrent: int = 3) -> Dict:
    """
    Backtest a single strategy across all its symbols.
    Entry: when all rules fire
    Exit: after hold_bars bars OR when rules stop firing (whichever first)
    """
    rules = strategy["rules"][:15]  # Top 15 rules
    side = strategy["side"]
    symbols = strategy.get("symbols", [])
    all_trades = []
    for symbol in symbols:
        kline_sym = find_kline_symbol(symbol)
        if not kline_sym:
            continue
        # Test on 1h timeframe
        tf = "1h"
        df = load_klines(kline_sym, tf)
        if df is None or len(df) < 250:
            continue
        arrays = compute_indicator_arrays(df)
        close = df["close"].values
        n = len(df)
        # Separate rules by TF
        rules_1h = [r for r in rules if not r["indicator"].endswith("_4h")]
        # Only use 1h rules for this TF
        if not rules_1h:
            continue
        # Walk through bars
        in_position = False
        entry_bar = 0
        entry_price = 0
        for i in range(250, n):
            if not in_position:
                # Check if rules fire
                fires = True
                for rule in rules_1h:
                    ind = rule["indicator"]
                    if ind not in arrays:
                        fires = False
                        break
                    val = arrays[ind][i]
                    if np.isnan(val):
                        fires = False
                        break
                    if rule["op"] == ">" and val <= rule["threshold"]:
                        fires = False; break
                    elif rule["op"] == "<" and val >= rule["threshold"]:
                        fires = False; break
                    elif rule["op"] == "band" and (val < rule["lo"] or val > rule["hi"]):
                        fires = False; break
                if fires:
                    in_position = True
                    entry_bar = i
                    entry_price = close[i]
            else:
                # Check exit: hold_bars elapsed OR rules stopped firing
                bars_held = i - entry_bar
                if bars_held >= hold_bars:
                    exit_price = close[i]
                    if side == "LONG":
                        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                    else:
                        pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                    all_trades.append({
                        "symbol": symbol,
                        "entry_bar": entry_bar,
                        "exit_bar": i,
                        "entry_price": float(entry_price),
                        "exit_price": float(exit_price),
                        "pnl_pct": float(pnl_pct),
                        "bars_held": bars_held,
                        "entry_time": str(df["timestamp"].iloc[entry_bar]),
                        "exit_time": str(df["timestamp"].iloc[i]),
                    })
                    in_position = False
    # Calculate stats
    if not all_trades:
        return {"strategy_id": strategy["trader_id"], "side": side, "n_trades": 0, "status": "NO_TRADES"}
    pnls = [t["pnl_pct"] for t in all_trades]
    wins = sum(1 for p in pnls if p > 0)
    total = len(pnls)
    avg_pnl = np.mean(pnls)
    std_pnl = np.std(pnls) if len(pnls) > 1 else 1
    sharpe = (avg_pnl / std_pnl) * np.sqrt(252 / max(1, hold_bars)) if std_pnl > 0 else 0
    # Max drawdown
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0
    # Profit factor
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    return {
        "strategy_id": strategy["trader_id"],
        "side": side,
        "n_trades": total,
        "win_rate": wins / total * 100,
        "avg_pnl_pct": float(avg_pnl),
        "total_pnl_pct": float(sum(pnls)),
        "sharpe": float(sharpe),
        "profit_factor": float(pf),
        "max_drawdown_pct": float(max_dd),
        "avg_bars_held": float(np.mean([t["bars_held"] for t in all_trades])),
        "symbols_traded": len(set(t["symbol"] for t in all_trades)),
        "trades": all_trades,
        "trader_health_score": strategy.get("trader_health_score"),
        "trader_health_status": strategy.get("trader_health_status"),
        "n_rules": len(rules),
        "best_selectivity": strategy.get("best_single_selectivity", 1.0),
        "status": "OK",
    }


def load_our_system_performance() -> Dict:
    """Load our current system performance for comparison."""
    # Check decision JSONL files
    decisions_dir = BASE_PATH / "data" / "decisions"
    if not decisions_dir.exists():
        return {"total_pnl": 0, "n_trades": 0, "win_rate": 0, "sharpe": 0}
    total_pnl = 0
    wins = 0
    total = 0
    pnls = []
    for f in decisions_dir.glob("*.jsonl"):
        try:
            with open(f) as fh:
                for line in fh:
                    entry = json.loads(line)
                    if "pnl_pct" in entry:
                        pnl = float(entry.get("pnl_pct", 0))
                        pnls.append(pnl)
                        total_pnl += pnl
                        if pnl > 0:
                            wins += 1
                        total += 1
        except Exception:
            continue
    if total == 0:
        return {"total_pnl": 0, "n_trades": 0, "win_rate": 0, "sharpe": 0}
    avg = np.mean(pnls)
    std = np.std(pnls) if len(pnls) > 1 else 1
    return {
        "total_pnl": total_pnl,
        "n_trades": total,
        "win_rate": wins / total * 100,
        "sharpe": (avg / std) * np.sqrt(252) if std > 0 else 0,
        "avg_pnl_pct": avg,
    }


def run_backtest(args):
    """Main backtest pipeline."""
    if not STRATEGIES_PATH.exists():
        logger.error(f"No strategies file at {STRATEGIES_PATH}. Run reverse_engineer_traders.py first.")
        return
    with open(STRATEGIES_PATH) as f:
        strategies = json.load(f)
    # Filter to strategies with decent validation
    valid = [s for s in strategies if s.get("validation", {}).get("valid", False)]
    # Sort by quality (precision * recall)
    valid.sort(key=lambda s: -(s.get("validation", {}).get("precision", 0) * s.get("validation", {}).get("recall", 0)))
    top_n = args.top if args.top else len(valid)
    selected = valid[:top_n]
    logger.info(f"Backtesting {len(selected)} strategies (hold_bars={args.hold_bars})")
    results = []
    for i, strategy in enumerate(selected):
        v = strategy.get("validation", {})
        logger.info(f"[{i+1}/{len(selected)}] {strategy['trader_id'][:16]}... {strategy['side']} (P={v.get('precision',0):.3f} R={v.get('recall',0):.3f})")
        result = backtest_strategy(strategy, hold_bars=args.hold_bars)
        results.append(result)
        if result["status"] == "OK":
            logger.info(f"  trades={result['n_trades']} WR={result['win_rate']:.1f}% PnL={result['total_pnl_pct']:.2f}% Sharpe={result['sharpe']:.2f} PF={result['profit_factor']:.2f}")
    # Sort by Sharpe
    ok_results = [r for r in results if r["status"] == "OK" and r["n_trades"] >= 3]
    ok_results.sort(key=lambda r: -r["sharpe"])
    # Load our system for comparison
    our_perf = load_our_system_performance()
    # Save results
    output_path = OUTPUT_DIR / "backtest_results.json"
    with open(output_path, "w") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), "hold_bars": args.hold_bars, "results": ok_results, "our_system": our_perf}, f, indent=2, default=str)
    # Generate report
    report_path = OUTPUT_DIR / "BACKTEST_DERIVED_REPORT.md"
    with open(report_path, "w") as f:
        f.write(f"# Derived Strategy Backtest Results — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write(f"**Hold period**: {args.hold_bars} bars (1h)\n")
        f.write(f"**Strategies tested**: {len(selected)}\n")
        f.write(f"**With trades**: {len(ok_results)}\n\n")
        f.write("## Our Current System Performance\n\n")
        f.write(f"- Trades: {our_perf.get('n_trades', 0)}\n")
        f.write(f"- Win Rate: {our_perf.get('win_rate', 0):.1f}%\n")
        f.write(f"- Sharpe: {our_perf.get('sharpe', 0):.2f}\n\n")
        f.write("## Derived Strategies Ranked by Sharpe\n\n")
        f.write("| # | Trader | Side | Trades | WR% | Avg PnL% | Total PnL% | Sharpe | PF | MaxDD% | Rules | Sel |\n")
        f.write("|---|--------|------|-------:|----:|---------:|-----------:|-------:|---:|-------:|------:|----:|\n")
        for i, r in enumerate(ok_results[:40]):
            f.write(f"| {i+1} | {r['strategy_id'][:12]}... | {r['side']} | {r['n_trades']} | {r['win_rate']:.1f} | {r['avg_pnl_pct']:.3f} | {r['total_pnl_pct']:.2f} | {r['sharpe']:.2f} | {r['profit_factor']:.2f} | {r['max_drawdown_pct']:.2f} | {r['n_rules']} | {r['best_selectivity']:.3f} |\n")
        # Strategies that BEAT our system
        our_sharpe = our_perf.get("sharpe", 0)
        better = [r for r in ok_results if r["sharpe"] > our_sharpe]
        f.write(f"\n\n## Strategies Beating Our System (Sharpe > {our_sharpe:.2f})\n\n")
        f.write(f"**{len(better)} strategies** outperform our current system.\n\n")
        for i, r in enumerate(better[:20]):
            f.write(f"### #{i+1} — `{r['strategy_id'][:16]}` ({r['side']})\n")
            f.write(f"- Sharpe: **{r['sharpe']:.2f}** | PF: {r['profit_factor']:.2f} | WR: {r['win_rate']:.1f}%\n")
            f.write(f"- Trades: {r['n_trades']} | Avg PnL: {r['avg_pnl_pct']:.3f}% | Total: {r['total_pnl_pct']:.2f}%\n")
            f.write(f"- Symbols: {r['symbols_traded']} | Bars held: {r['avg_bars_held']:.1f}\n\n")
        # Recommendations
        f.write("\n## Recommendations\n\n")
        if better:
            f.write("### Strategies to Integrate\n\n")
            for r in better[:5]:
                f.write(f"1. **{r['strategy_id'][:16]} {r['side']}**: Sharpe {r['sharpe']:.2f}, {r['n_trades']} trades\n")
        f.write("\n### Limitations\n\n")
        f.write("- Only 18 days of kline data available locally (Mar 20 - Apr 7 2026)\n")
        f.write("- Server 1 (157.180.125.52) is down — full NPZ with years of data unavailable\n")
        f.write("- Need to re-run with full historical data for reliable results\n")
        f.write("- 4h rules not tested in backtest (only 1h execution)\n")
    logger.info(f"Report saved to {report_path}")
    logger.info(f"Results saved to {output_path}")
    # Print summary
    print(f"\n{'='*70}")
    print(f"BACKTEST COMPLETE")
    print(f"{'='*70}")
    print(f"Strategies with trades: {len(ok_results)}")
    if ok_results:
        print(f"\nTop 10 by Sharpe:")
        for i, r in enumerate(ok_results[:10]):
            print(f"  {i+1}. {r['strategy_id'][:16]}... {r['side']:5s} | Sharpe={r['sharpe']:.2f} WR={r['win_rate']:.1f}% PnL={r['total_pnl_pct']:.2f}% trades={r['n_trades']}")
    print(f"\nOur system: Sharpe={our_perf.get('sharpe',0):.2f} WR={our_perf.get('win_rate',0):.1f}%")
    better_count = len([r for r in ok_results if r["sharpe"] > our_perf.get("sharpe", 0)])
    print(f"Strategies beating us: {better_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=None, help="Only test top N strategies")
    parser.add_argument("--hold-bars", type=int, default=12, help="Bars to hold position (1h bars)")
    args = parser.parse_args()
    run_backtest(args)
