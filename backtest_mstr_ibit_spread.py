#!/usr/bin/env python3
"""
MSTR/IBIT Spread Backtest — mean-reversion pairs trade on BTC exposure divergence.

Strategy:
  IBIT = direct BTC spot ETF (tight to NAV, low tracking error)
  MSTR = leveraged BTC proxy (~200k+ BTC on balance sheet, trades at oscillating premium/discount)
  When MSTR/IBIT ratio diverges from mean → trade the convergence.

Entry: ratio z-score exceeds threshold → short the overperformer, long the underperformer
Exit:  ratio reverts to mean (z~0) or hits stop-loss

Phase 1: Underlying pairs trade (daily bars, dollar-neutral)
Phase 2: Options version in separate file

Tests across: lookback windows, z-score thresholds, hold times, position sizing
"""
import json
import math
import sys
import statistics
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(BASE))


def load_daily(symbol):
    path = BASE / "klines_cache" / "tradier" / f"{symbol}_D.json"
    with open(path) as f:
        bars = json.load(f)
    for b in bars:
        ts = b["timestamp"]
        if "T" in ts:
            ts = ts.replace("T", " ")
        b["timestamp"] = ts[:10]
    return bars


def align_bars(mstr_bars, ibit_bars):
    ibit_map = {b["timestamp"]: b for b in ibit_bars}
    aligned = []
    for m in mstr_bars:
        ts = m["timestamp"]
        if ts in ibit_map:
            ib = ibit_map[ts]
            if ib["close"] > 0 and m["close"] > 0:
                aligned.append({
                    "date": ts,
                    "mstr_close": m["close"],
                    "ibit_close": ib["close"],
                    "mstr_high": m["high"],
                    "mstr_low": m["low"],
                    "ibit_high": ib["high"],
                    "ibit_low": ib["low"],
                    "mstr_volume": m.get("volume", 0),
                    "ibit_volume": ib.get("volume", 0),
                    "ratio": m["close"] / ib["close"],
                })
    return aligned


def compute_zscore(ratios, lookback):
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


def run_backtest(aligned, lookback=20, z_entry=1.5, z_exit=0.3, z_stop=3.5,
                 max_hold_days=30, position_size=5000):
    """
    Dollar-neutral pairs trade on MSTR/IBIT ratio.
    When z > z_entry: SHORT MSTR + LONG IBIT (MSTR premium overextended)
    When z < -z_entry: LONG MSTR + SHORT IBIT (MSTR discount overextended)
    Exit when z crosses z_exit (mean reversion) or hits z_stop.
    """
    ratios = [a["ratio"] for a in aligned]
    zscores = compute_zscore(ratios, lookback)
    trades = []
    position = None
    for i in range(lookback, len(aligned)):
        bar = aligned[i]
        z = zscores[i]
        # Exit check
        if position:
            hold = i - position["entry_bar"]
            exit_reason = None
            if position["direction"] == "SHORT_MSTR":
                if z <= z_exit:
                    exit_reason = "REVERT"
                elif z >= z_stop:
                    exit_reason = "STOP"
                elif hold >= max_hold_days:
                    exit_reason = "MAX_HOLD"
            else:
                if z >= -z_exit:
                    exit_reason = "REVERT"
                elif z <= -z_stop:
                    exit_reason = "STOP"
                elif hold >= max_hold_days:
                    exit_reason = "MAX_HOLD"
            if exit_reason:
                if position["direction"] == "SHORT_MSTR":
                    mstr_pnl = (position["mstr_entry"] - bar["mstr_close"]) * position["mstr_qty"]
                    ibit_pnl = (bar["ibit_close"] - position["ibit_entry"]) * position["ibit_qty"]
                else:
                    mstr_pnl = (bar["mstr_close"] - position["mstr_entry"]) * position["mstr_qty"]
                    ibit_pnl = (position["ibit_entry"] - bar["ibit_close"]) * position["ibit_qty"]
                total_pnl = mstr_pnl + ibit_pnl
                capital_used = position_size * 2
                trades.append({
                    "direction": position["direction"],
                    "entry_date": position["entry_date"],
                    "exit_date": bar["date"],
                    "entry_z": position["entry_z"],
                    "exit_z": z,
                    "hold_days": hold,
                    "mstr_pnl": mstr_pnl,
                    "ibit_pnl": ibit_pnl,
                    "total_pnl": total_pnl,
                    "pnl_pct": total_pnl / capital_used * 100,
                    "exit_reason": exit_reason,
                    "mstr_move_pct": (bar["mstr_close"] - position["mstr_entry"]) / position["mstr_entry"] * 100,
                    "ibit_move_pct": (bar["ibit_close"] - position["ibit_entry"]) / position["ibit_entry"] * 100,
                })
                position = None
        # Entry check
        if position is None:
            if z > z_entry:
                mstr_qty = int(position_size / bar["mstr_close"])
                ibit_qty = int(position_size / bar["ibit_close"])
                if mstr_qty > 0 and ibit_qty > 0:
                    position = {
                        "direction": "SHORT_MSTR",
                        "entry_bar": i,
                        "entry_date": bar["date"],
                        "entry_z": z,
                        "mstr_entry": bar["mstr_close"],
                        "ibit_entry": bar["ibit_close"],
                        "mstr_qty": mstr_qty,
                        "ibit_qty": ibit_qty,
                    }
            elif z < -z_entry:
                mstr_qty = int(position_size / bar["mstr_close"])
                ibit_qty = int(position_size / bar["ibit_close"])
                if mstr_qty > 0 and ibit_qty > 0:
                    position = {
                        "direction": "LONG_MSTR",
                        "entry_bar": i,
                        "entry_date": bar["date"],
                        "entry_z": z,
                        "mstr_entry": bar["mstr_close"],
                        "ibit_entry": bar["ibit_close"],
                        "mstr_qty": mstr_qty,
                        "ibit_qty": ibit_qty,
                    }
    return trades


def analyze_trades(trades, label=""):
    if not trades:
        print(f"  {label}: NO TRADES")
        return None
    pnls = [t["total_pnl"] for t in trades]
    total = sum(pnls)
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    wr = len(winners) / len(pnls) * 100
    avg_win = statistics.mean(winners) if winners else 0
    avg_loss = statistics.mean(losers) if losers else 0
    sharpe = (statistics.mean(pnls) / statistics.stdev(pnls)) if len(pnls) > 1 and statistics.stdev(pnls) > 0 else 0
    avg_hold = statistics.mean([t["hold_days"] for t in trades])
    max_dd = min(pnls)
    max_win = max(pnls)
    by_reason = defaultdict(list)
    by_dir = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]].append(t["total_pnl"])
        by_dir[t["direction"]].append(t["total_pnl"])
    pnl_pcts = [t["pnl_pct"] for t in trades]
    avg_ret = statistics.mean(pnl_pcts)
    print(f"  {label}")
    print(f"    Trades: {len(trades)} | P/L: ${total:+,.0f} | WR: {wr:.0f}% | Sharpe: {sharpe:.2f} | Avg ret: {avg_ret:+.2f}%")
    print(f"    Avg hold: {avg_hold:.1f}d | Max win: ${max_win:+,.0f} | Max loss: ${max_dd:+,.0f}")
    print(f"    Avg win: ${avg_win:+,.0f} | Avg loss: ${avg_loss:+,.0f}")
    for reason, pnl_list in sorted(by_reason.items()):
        wr_r = len([p for p in pnl_list if p > 0]) / len(pnl_list) * 100
        print(f"      {reason}: {len(pnl_list)} trades, ${sum(pnl_list):+,.0f}, WR {wr_r:.0f}%")
    for d, pnl_list in sorted(by_dir.items()):
        wr_d = len([p for p in pnl_list if p > 0]) / len(pnl_list) * 100
        print(f"      {d}: {len(pnl_list)} trades, ${sum(pnl_list):+,.0f}, WR {wr_d:.0f}%")
    return {"sharpe": sharpe, "total_pnl": total, "n": len(trades), "wr": wr, "avg_ret": avg_ret, "label": label, "avg_hold": avg_hold}


def main():
    print("=" * 90)
    print("MSTR/IBIT SPREAD BACKTEST — BTC Exposure Divergence Pairs Trade")
    print("=" * 90)
    print("\nLoading daily data...")
    mstr_bars = load_daily("MSTR")
    ibit_bars = load_daily("IBIT")
    aligned = align_bars(mstr_bars, ibit_bars)
    print(f"Aligned: {len(aligned)} trading days ({aligned[0]['date']} -> {aligned[-1]['date']})")
    ratios = [a["ratio"] for a in aligned]
    print(f"MSTR/IBIT ratio: min={min(ratios):.2f} max={max(ratios):.2f} mean={statistics.mean(ratios):.2f} std={statistics.stdev(ratios):.2f}")
    # Show ratio evolution in chunks
    chunk = len(aligned) // 4
    for idx in range(4):
        start = idx * chunk
        end = min(start + chunk, len(aligned))
        chunk_ratios = ratios[start:end]
        print(f"  {aligned[start]['date']}-{aligned[end-1]['date']}: mean={statistics.mean(chunk_ratios):.2f} std={statistics.stdev(chunk_ratios):.2f}")
    print(f"\n{'=' * 90}")
    print("PARAMETER SWEEP")
    print(f"{'=' * 90}\n")
    results = []
    for lookback in [10, 15, 20, 30, 40, 60]:
        for z_entry in [1.0, 1.5, 2.0, 2.5, 3.0]:
            for z_exit in [0.0, 0.3, 0.5]:
                for z_stop in [3.0, 4.0, 5.0]:
                    for max_hold in [10, 20, 30, 60]:
                        label = f"LB={lookback:>2} Ze={z_entry} Zx={z_exit} Zs={z_stop} MH={max_hold:>2}"
                        trades = run_backtest(aligned, lookback=lookback, z_entry=z_entry,
                                              z_exit=z_exit, z_stop=z_stop, max_hold_days=max_hold)
                        if trades and len(trades) >= 3:
                            r = analyze_trades(trades, label)
                            if r:
                                results.append(r)
    if not results:
        print("NO CONFIGS PRODUCED 3+ TRADES")
        return
    # Sort by Sharpe
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    print(f"\n{'=' * 90}")
    print(f"TOP 20 BY SHARPE (min 3 trades)")
    print(f"{'=' * 90}")
    for r in results[:20]:
        print(f"  Sharpe={r['sharpe']:>6.2f} P/L=${r['total_pnl']:>+9,.0f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgRet={r['avg_ret']:>+6.2f}% Hold={r['avg_hold']:>4.1f}d  {r['label']}")
    # Sort by P/L
    results.sort(key=lambda x: x["total_pnl"], reverse=True)
    print(f"\nTOP 20 BY P/L")
    for r in results[:20]:
        print(f"  P/L=${r['total_pnl']:>+9,.0f} Sharpe={r['sharpe']:>6.2f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgRet={r['avg_ret']:>+6.2f}% Hold={r['avg_hold']:>4.1f}d  {r['label']}")
    # Sort by WR (min 5 trades)
    filtered = [r for r in results if r["n"] >= 5]
    if filtered:
        filtered.sort(key=lambda x: x["wr"], reverse=True)
        print(f"\nTOP 10 BY WIN RATE (min 5 trades)")
        for r in filtered[:10]:
            print(f"  WR={r['wr']:>4.0f}% Sharpe={r['sharpe']:>6.2f} P/L=${r['total_pnl']:>+9,.0f} Trades={r['n']:>3} AvgRet={r['avg_ret']:>+6.2f}% Hold={r['avg_hold']:>4.1f}d  {r['label']}")
    # Best overall: Sharpe > 0.3 AND WR > 55% AND P/L > 0
    best = [r for r in results if r["sharpe"] > 0.3 and r["wr"] > 55 and r["total_pnl"] > 0 and r["n"] >= 5]
    if best:
        best.sort(key=lambda x: x["sharpe"] * x["total_pnl"], reverse=True)
        print(f"\nBEST BALANCED (Sharpe>0.3, WR>55%, P/L>0, 5+ trades)")
        for r in best[:10]:
            print(f"  Sharpe={r['sharpe']:>6.2f} WR={r['wr']:>4.0f}% P/L=${r['total_pnl']:>+9,.0f} Trades={r['n']:>3} AvgRet={r['avg_ret']:>+6.2f}%  {r['label']}")
    # Show individual trades for best config
    if results:
        best_cfg = results[0]  # top by P/L
        parts = best_cfg["label"].split()
        lb = int(parts[0].split("=")[1])
        ze = float(parts[1].split("=")[1])
        zx = float(parts[2].split("=")[1])
        zs = float(parts[3].split("=")[1])
        mh = int(parts[4].split("=")[1])
        print(f"\n{'=' * 90}")
        print(f"TRADE LOG — Best P/L config: {best_cfg['label']}")
        print(f"{'=' * 90}")
        trades = run_backtest(aligned, lookback=lb, z_entry=ze, z_exit=zx, z_stop=zs, max_hold_days=mh)
        cum_pnl = 0
        for t in trades:
            cum_pnl += t["total_pnl"]
            print(f"  {t['entry_date']} -> {t['exit_date']} | {t['direction']:<12} | z: {t['entry_z']:>+5.2f}->{t['exit_z']:>+5.2f} | {t['exit_reason']:<9} | P/L: ${t['total_pnl']:>+7,.0f} ({t['pnl_pct']:>+5.1f}%) | MSTR {t['mstr_move_pct']:>+5.1f}% IBIT {t['ibit_move_pct']:>+5.1f}% | Cum: ${cum_pnl:>+8,.0f}")


if __name__ == "__main__":
    main()
