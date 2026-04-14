#!/usr/bin/env python3
"""
USO/BNO Spread Backtest — mean-reversion pairs trade on oil ETF divergence.

Strategy:
  USO = front-month WTI (more volatile, contango drag)
  BNO = Brent blend, longer-dated (smoother)
  When ratio diverges from mean → trade the convergence.

Entry: ratio z-score exceeds threshold → short the overperformer, long the underperformer
Exit:  ratio reverts to mean (z=0) or hits stop-loss

Tests across: lookback windows, z-score thresholds, hold times, intraday vs swing
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import statistics

BASE = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(BASE))

def load_bars(symbol):
    path = BASE / "klines_cache" / "tradier" / f"{symbol}_15m.json"
    with open(path) as f:
        bars = json.load(f)
    # Normalize timestamps
    for b in bars:
        ts = b["timestamp"]
        if "T" in ts:
            ts = ts.replace("T", " ")
        b["timestamp"] = ts[:16]  # YYYY-MM-DD HH:MM
    return bars


def align_bars(uso_bars, bno_bars):
    """Align USO and BNO bars by timestamp."""
    bno_map = {b["timestamp"]: b for b in bno_bars}
    aligned = []
    for u in uso_bars:
        ts = u["timestamp"]
        if ts in bno_map:
            b = bno_map[ts]
            aligned.append({
                "timestamp": ts,
                "uso_close": u["close"],
                "bno_close": b["close"],
                "uso_volume": u["volume"],
                "bno_volume": b["volume"],
                "uso_high": u["high"],
                "uso_low": u["low"],
                "bno_high": b["high"],
                "bno_low": b["low"],
                "ratio": u["close"] / b["close"] if b["close"] > 0 else 0,
                "date": ts[:10],
                "time": ts[11:16]
            })
    return aligned


def compute_zscore(ratios, lookback):
    """Rolling z-score of the ratio."""
    zscores = []
    for i in range(len(ratios)):
        if i < lookback:
            zscores.append(0)
            continue
        window = ratios[i - lookback:i]
        mean = statistics.mean(window)
        std = statistics.stdev(window) if len(window) > 1 else 0.001
        if std < 0.0001:
            std = 0.0001
        z = (ratios[i] - mean) / std
        zscores.append(z)
    return zscores


def run_backtest(aligned, lookback=40, z_entry=1.5, z_exit=0.3, z_stop=3.0,
                 max_hold_bars=26*5, intraday_only=False, position_size=2000):
    """
    Run spread backtest.
    When z > z_entry: short USO, long BNO (ratio will contract)
    When z < -z_entry: long USO, short BNO (ratio will expand back)
    Exit when z crosses z_exit (mean reversion) or hits z_stop (blowout)
    """
    ratios = [a["ratio"] for a in aligned]
    zscores = compute_zscore(ratios, lookback)
    trades = []
    position = None  # {"direction": "SHORT_SPREAD"/"LONG_SPREAD", "entry_z": z, "entry_bar": i, ...}
    for i in range(lookback, len(aligned)):
        bar = aligned[i]
        z = zscores[i]
        # Intraday only: close at EOD
        if intraday_only and position and bar["date"] != position["entry_date"]:
            # Force close at EOD
            uso_pnl = (position["uso_entry"] - bar["uso_close"]) * position["uso_qty"] if position["direction"] == "SHORT_SPREAD" else (bar["uso_close"] - position["uso_entry"]) * position["uso_qty"]
            bno_pnl = (bar["bno_close"] - position["bno_entry"]) * position["bno_qty"] if position["direction"] == "SHORT_SPREAD" else (position["bno_entry"] - bar["bno_close"]) * position["bno_qty"]
            trades.append({
                "direction": position["direction"],
                "entry_time": position["entry_time"],
                "exit_time": bar["timestamp"],
                "entry_z": position["entry_z"],
                "exit_z": z,
                "hold_bars": i - position["entry_bar"],
                "uso_pnl": uso_pnl,
                "bno_pnl": bno_pnl,
                "total_pnl": uso_pnl + bno_pnl,
                "exit_reason": "EOD"
            })
            position = None
        # Check exits
        if position:
            hold_bars = i - position["entry_bar"]
            exit_reason = None
            if position["direction"] == "SHORT_SPREAD":
                if z <= z_exit:
                    exit_reason = "MEAN_REVERT"
                elif z >= z_stop:
                    exit_reason = "STOP_LOSS"
                elif hold_bars >= max_hold_bars:
                    exit_reason = "MAX_HOLD"
            else:  # LONG_SPREAD
                if z >= -z_exit:
                    exit_reason = "MEAN_REVERT"
                elif z <= -z_stop:
                    exit_reason = "STOP_LOSS"
                elif hold_bars >= max_hold_bars:
                    exit_reason = "MAX_HOLD"
            if exit_reason:
                uso_pnl = (position["uso_entry"] - bar["uso_close"]) * position["uso_qty"] if position["direction"] == "SHORT_SPREAD" else (bar["uso_close"] - position["uso_entry"]) * position["uso_qty"]
                bno_pnl = (bar["bno_close"] - position["bno_entry"]) * position["bno_qty"] if position["direction"] == "SHORT_SPREAD" else (position["bno_entry"] - bar["bno_close"]) * position["bno_qty"]
                trades.append({
                    "direction": position["direction"],
                    "entry_time": position["entry_time"],
                    "exit_time": bar["timestamp"],
                    "entry_z": position["entry_z"],
                    "exit_z": z,
                    "hold_bars": hold_bars,
                    "uso_pnl": uso_pnl,
                    "bno_pnl": bno_pnl,
                    "total_pnl": uso_pnl + bno_pnl,
                    "exit_reason": exit_reason
                })
                position = None
        # Check entries (only if flat)
        if position is None:
            if z > z_entry:
                # Ratio expanded too much — short USO, long BNO
                uso_qty = int(position_size / bar["uso_close"])
                bno_qty = int(position_size / bar["bno_close"])
                position = {
                    "direction": "SHORT_SPREAD",
                    "entry_bar": i,
                    "entry_time": bar["timestamp"],
                    "entry_date": bar["date"],
                    "entry_z": z,
                    "uso_entry": bar["uso_close"],
                    "bno_entry": bar["bno_close"],
                    "uso_qty": uso_qty,
                    "bno_qty": bno_qty
                }
            elif z < -z_entry:
                # Ratio contracted too much — long USO, short BNO
                uso_qty = int(position_size / bar["uso_close"])
                bno_qty = int(position_size / bar["bno_close"])
                position = {
                    "direction": "LONG_SPREAD",
                    "entry_bar": i,
                    "entry_time": bar["timestamp"],
                    "entry_date": bar["date"],
                    "entry_z": z,
                    "uso_entry": bar["uso_close"],
                    "bno_entry": bar["bno_close"],
                    "uso_qty": uso_qty,
                    "bno_qty": bno_qty
                }
    return trades


def analyze_trades(trades, label=""):
    if not trades:
        print(f"  {label}: NO TRADES")
        return {"total_pnl": 0, "n_trades": 0, "win_rate": 0, "sharpe": 0}
    pnls = [t["total_pnl"] for t in trades]
    total = sum(pnls)
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    win_rate = len(winners) / len(pnls) * 100
    avg_win = statistics.mean(winners) if winners else 0
    avg_loss = statistics.mean(losers) if losers else 0
    sharpe = (statistics.mean(pnls) / statistics.stdev(pnls)) if len(pnls) > 1 and statistics.stdev(pnls) > 0 else 0
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]].append(t["total_pnl"])
    avg_hold = statistics.mean([t["hold_bars"] for t in trades])
    print(f"  {label}")
    print(f"    Trades: {len(trades)}  |  P/L: ${total:+,.0f}  |  WR: {win_rate:.0f}%  |  Sharpe: {sharpe:.2f}")
    print(f"    Avg win: ${avg_win:+,.0f}  |  Avg loss: ${avg_loss:+,.0f}  |  Avg hold: {avg_hold:.0f} bars ({avg_hold*15/60:.1f}h)")
    for reason, pnl_list in sorted(by_reason.items()):
        print(f"    {reason}: {len(pnl_list)} trades, ${sum(pnl_list):+,.0f}")
    return {"total_pnl": total, "n_trades": len(trades), "win_rate": win_rate, "sharpe": sharpe, "label": label}


def main():
    print("Loading data...")
    uso_bars = load_bars("USO")
    bno_bars = load_bars("BNO")
    aligned = align_bars(uso_bars, bno_bars)
    print(f"Aligned: {len(aligned)} bars ({aligned[0]['date']} → {aligned[-1]['date']})")
    ratios = [a["ratio"] for a in aligned]
    print(f"Ratio: min={min(ratios):.4f} max={max(ratios):.4f} mean={statistics.mean(ratios):.4f} std={statistics.stdev(ratios):.4f}")
    print(f"\n{'='*80}")
    print("SWEEP: lookback × z_entry × mode")
    print(f"{'='*80}\n")
    results = []
    for lookback in [20, 40, 60, 100]:
        for z_entry in [1.0, 1.5, 2.0, 2.5]:
            for z_exit in [0.0, 0.3, 0.5]:
                for intraday in [True, False]:
                    mode = "INTRADAY" if intraday else "SWING"
                    label = f"LB={lookback:>3} Z_entry={z_entry} Z_exit={z_exit} {mode}"
                    trades = run_backtest(aligned, lookback=lookback, z_entry=z_entry,
                                        z_exit=z_exit, intraday_only=intraday)
                    if trades:
                        r = analyze_trades(trades, label)
                        results.append(r)
    # Sort by Sharpe
    results.sort(key=lambda x: x.get("sharpe", 0), reverse=True)
    print(f"\n{'='*80}")
    print("TOP 10 BY SHARPE")
    print(f"{'='*80}")
    for r in results[:10]:
        print(f"  {r['label']:<50} Sharpe={r['sharpe']:>6.2f}  P/L=${r['total_pnl']:>+8,.0f}  Trades={r['n_trades']:>3}  WR={r['win_rate']:>4.0f}%")
    # Also sort by total P/L
    results.sort(key=lambda x: x.get("total_pnl", 0), reverse=True)
    print(f"\nTOP 10 BY P/L")
    for r in results[:10]:
        print(f"  {r['label']:<50} P/L=${r['total_pnl']:>+8,.0f}  Sharpe={r['sharpe']:>6.2f}  Trades={r['n_trades']:>3}  WR={r['win_rate']:>4.0f}%")


if __name__ == "__main__":
    main()
