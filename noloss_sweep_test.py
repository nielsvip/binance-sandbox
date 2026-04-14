"""
NOLOSS threshold sweep test for WT/DC scorer system.
Tests effect of minimum-gain-before-exit-allowed on backtest performance.

Entry: score_entry >= 37 (both LONG and SHORT)
Exit: score_exit >= 20, BUT only if gain >= noloss_threshold
Trading hours: 13:30-20:00 UTC weekdays
Data: backtest_v8/indicators/ NPZ, 5m bars, sample every 2nd bar
Capital: $180k, equal-weight per symbol
Start: 2024-01-01 (or first bar available)
"""

import os
import sys
import time
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, "/Users/niels/Documents/binance")
from wt_dc_entry_scorer import score_entry
from wt_dc_exit_scorer import score_exit

BASE = "/Users/niels/Documents/binance"
NPZ_DIR = os.path.join(BASE, "backtest_v8/indicators")
SYMBOLS = ["MU", "AAPL", "TTD", "FIVN", "AMZN", "MRVL", "XOM", "CVX", "GLD", "USO", "NVDA", "MSFT", "ASTS"]
ENTRY_THRESHOLD = 37
EXIT_THRESHOLD = 20
CAPITAL = 180_000.0
POSITION_SIZE = CAPITAL / len(SYMBOLS)  # equal weight
NOLOSS_LEVELS = [0.0, 0.1, 0.3, 0.5, 1.0, 2.0, 3.0, -1.0, -3.0]
START_TS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
BAR_STEP = 2  # sample every 2nd bar


def is_trading_hour(ts):
    """Check if timestamp falls in 13:30-20:00 UTC on a weekday."""
    dt = datetime.utcfromtimestamp(ts)
    if dt.weekday() >= 5:
        return False
    minutes = dt.hour * 60 + dt.minute
    return 810 <= minutes < 1200  # 13:30=810, 20:00=1200


def load_symbol(symbol):
    """Load NPZ and return dict of arrays + timestamps."""
    path = os.path.join(NPZ_DIR, f"{symbol}.npz")
    f = np.load(path, allow_pickle=True)
    data = {}
    for k in f.files:
        arr = f[k]
        if arr.dtype == np.int64 or arr.dtype == np.float64:
            data[k] = arr
        else:
            data[k] = arr.astype(np.float32)
    return data


def run_backtest(symbol, data, noloss_pct):
    """Run backtest for one symbol with given noloss threshold.
    Returns list of trade dicts."""
    ts = data["timestamps"]
    close = data["close"]
    n = len(ts)
    start_idx = max(0, int(np.searchsorted(ts, START_TS)))
    # Skip warmup
    start_idx = max(start_idx, 500)
    keys = list(data.keys())
    trades = []
    position = None  # {'side': 'LONG'/'SHORT', 'entry_price': float, 'entry_idx': int, 'entry_ts': int}
    noloss_thresh = noloss_pct / 100.0  # convert % to ratio
    for i in range(start_idx, n, BAR_STEP):
        t = int(ts[i])
        if not is_trading_hour(t):
            continue
        price = float(close[i])
        if price <= 0 or np.isnan(price):
            continue
        # Build indicator dict for this bar
        ind = {}
        for k in keys:
            if k == "timestamps":
                continue
            v = float(data[k][i])
            if np.isnan(v) or np.isinf(v):
                v = 0.0
            ind[k] = v
        if position is not None:
            # Check exit
            is_long = position["side"] == "LONG"
            if is_long:
                gain = (price - position["entry_price"]) / position["entry_price"]
            else:
                gain = (position["entry_price"] - price) / position["entry_price"]
            # NOLOSS gate: only allow exit if gain >= threshold
            if gain >= noloss_thresh:
                exit_score, exit_reason = score_exit(ind, is_long, price)
                if exit_score >= EXIT_THRESHOLD:
                    pnl_pct = gain * 100.0
                    pnl_dollar = gain * POSITION_SIZE
                    trades.append({
                        "symbol": symbol,
                        "side": position["side"],
                        "entry_price": position["entry_price"],
                        "exit_price": price,
                        "entry_ts": position["entry_ts"],
                        "exit_ts": t,
                        "pnl_pct": pnl_pct,
                        "pnl_dollar": pnl_dollar,
                        "hold_bars": (i - position["entry_idx"]) // BAR_STEP,
                        "exit_score": exit_score,
                    })
                    position = None
                    continue
            # Force exit if held too long (max 5 trading days = ~390 bars at 5m, step 2 = ~195)
            hold_bars = (i - position["entry_idx"]) // BAR_STEP
            if hold_bars > 200:
                if is_long:
                    gain = (price - position["entry_price"]) / position["entry_price"]
                else:
                    gain = (position["entry_price"] - price) / position["entry_price"]
                pnl_pct = gain * 100.0
                pnl_dollar = gain * POSITION_SIZE
                trades.append({
                    "symbol": symbol,
                    "side": position["side"],
                    "entry_price": position["entry_price"],
                    "exit_price": price,
                    "entry_ts": position["entry_ts"],
                    "exit_ts": t,
                    "pnl_pct": pnl_pct,
                    "pnl_dollar": pnl_dollar,
                    "hold_bars": hold_bars,
                    "exit_score": -1,  # forced
                })
                position = None
                continue
        else:
            # Check entry - try both LONG and SHORT, take higher score
            long_score, long_reason = score_entry(ind, is_long=True, current_price=price)
            short_score, short_reason = score_entry(ind, is_long=False, current_price=price)
            if long_score >= ENTRY_THRESHOLD and long_score >= short_score:
                position = {
                    "side": "LONG",
                    "entry_price": price,
                    "entry_idx": i,
                    "entry_ts": t,
                    "entry_score": long_score,
                }
            elif short_score >= ENTRY_THRESHOLD:
                position = {
                    "side": "SHORT",
                    "entry_price": price,
                    "entry_idx": i,
                    "entry_ts": t,
                    "entry_score": short_score,
                }
    return trades


def compute_metrics(trades):
    """Compute performance metrics from trade list."""
    if not trades:
        return {
            "n_trades": 0, "total_pnl": 0, "avg_pnl_pct": 0,
            "win_rate": 0, "profit_factor": 0, "max_dd_trade": 0,
            "weekly_sharpe": 0, "avg_hold": 0,
            "long_trades": 0, "short_trades": 0,
            "long_pnl": 0, "short_pnl": 0,
        }
    pnls = np.array([t["pnl_dollar"] for t in trades])
    pnl_pcts = np.array([t["pnl_pct"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    n = len(trades)
    total_pnl = float(np.sum(pnls))
    avg_pnl_pct = float(np.mean(pnl_pcts))
    wr = len(wins) / n * 100 if n > 0 else 0
    pf = float(np.sum(wins) / abs(np.sum(losses))) if len(losses) > 0 and np.sum(losses) != 0 else 999.0
    max_dd = float(np.min(pnl_pcts)) if n > 0 else 0
    # Weekly Sharpe: group trades by week, compute weekly returns
    weekly_returns = {}
    for t in trades:
        dt = datetime.utcfromtimestamp(t["exit_ts"])
        week_key = dt.isocalendar()[:2]
        if week_key not in weekly_returns:
            weekly_returns[week_key] = 0.0
        weekly_returns[week_key] += t["pnl_dollar"]
    if len(weekly_returns) >= 2:
        wr_arr = np.array(list(weekly_returns.values()))
        weekly_sharpe = float(np.mean(wr_arr) / np.std(wr_arr) * np.sqrt(52)) if np.std(wr_arr) > 0 else 0
    else:
        weekly_sharpe = 0
    avg_hold = float(np.mean([t["hold_bars"] for t in trades]))
    long_trades = [t for t in trades if t["side"] == "LONG"]
    short_trades = [t for t in trades if t["side"] == "SHORT"]
    return {
        "n_trades": n,
        "total_pnl": total_pnl,
        "avg_pnl_pct": avg_pnl_pct,
        "win_rate": wr,
        "profit_factor": pf,
        "max_dd_trade": max_dd,
        "weekly_sharpe": weekly_sharpe,
        "avg_hold": avg_hold,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_pnl": sum(t["pnl_dollar"] for t in long_trades),
        "short_pnl": sum(t["pnl_dollar"] for t in short_trades),
    }


def main():
    print(f"Loading {len(SYMBOLS)} symbols...")
    sym_data = {}
    for s in SYMBOLS:
        sym_data[s] = load_symbol(s)
        print(f"  {s}: {len(sym_data[s]['timestamps'])} bars")
    results = {}
    for noloss in NOLOSS_LEVELS:
        print(f"\n{'='*60}")
        print(f"NOLOSS = {noloss}%")
        print(f"{'='*60}")
        all_trades = []
        sym_metrics = {}
        for s in SYMBOLS:
            t0 = time.time()
            trades = run_backtest(s, sym_data[s], noloss)
            elapsed = time.time() - t0
            m = compute_metrics(trades)
            sym_metrics[s] = m
            all_trades.extend(trades)
            print(f"  {s:6s}: {m['n_trades']:4d} trades, PnL=${m['total_pnl']:+10.0f}, "
                  f"WR={m['win_rate']:5.1f}%, PF={m['profit_factor']:5.2f}, "
                  f"Sharpe={m['weekly_sharpe']:+6.2f}, MaxDD={m['max_dd_trade']:+6.2f}% ({elapsed:.1f}s)")
        total_m = compute_metrics(all_trades)
        print(f"\n  TOTAL: {total_m['n_trades']:4d} trades, PnL=${total_m['total_pnl']:+10.0f}, "
              f"WR={total_m['win_rate']:5.1f}%, PF={total_m['profit_factor']:5.2f}, "
              f"Sharpe={total_m['weekly_sharpe']:+6.2f}, MaxDD={total_m['max_dd_trade']:+6.2f}%")
        print(f"  LONG: {total_m['long_trades']} trades, PnL=${total_m['long_pnl']:+.0f} | "
              f"SHORT: {total_m['short_trades']} trades, PnL=${total_m['short_pnl']:+.0f}")
        results[noloss] = {"total": total_m, "per_symbol": sym_metrics}
    # Write results file
    out_path = os.path.join(BASE, "noloss_test_results.txt")
    with open(out_path, "w") as f:
        f.write("NOLOSS THRESHOLD SWEEP — WT/DC Scorer System\n")
        f.write(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
        f.write(f"Entry threshold: {ENTRY_THRESHOLD}, Exit threshold: {EXIT_THRESHOLD}\n")
        f.write(f"Symbols: {', '.join(SYMBOLS)}\n")
        f.write(f"Capital: ${CAPITAL:,.0f}, Position size: ${POSITION_SIZE:,.0f}/symbol\n")
        f.write(f"Data: 5m bars, sample every {BAR_STEP}nd, trading hours 13:30-20:00 UTC\n")
        f.write(f"Max hold: 200 bars (~5 trading days)\n")
        f.write("=" * 120 + "\n\n")
        # Summary table
        f.write("SUMMARY TABLE\n")
        f.write("-" * 120 + "\n")
        f.write(f"{'NOLOSS%':>8s} {'Trades':>7s} {'TotalPnL':>12s} {'AvgPnL%':>9s} {'WR%':>7s} "
                f"{'PF':>7s} {'Sharpe':>8s} {'MaxDD%':>8s} {'AvgHold':>8s} {'LongPnL':>12s} {'ShortPnL':>12s}\n")
        f.write("-" * 120 + "\n")
        for noloss in sorted(NOLOSS_LEVELS):
            m = results[noloss]["total"]
            f.write(f"{noloss:>8.1f} {m['n_trades']:>7d} ${m['total_pnl']:>+11,.0f} "
                    f"{m['avg_pnl_pct']:>+8.3f}% {m['win_rate']:>6.1f}% "
                    f"{m['profit_factor']:>6.2f} {m['weekly_sharpe']:>+7.2f} "
                    f"{m['max_dd_trade']:>+7.2f}% {m['avg_hold']:>7.1f} "
                    f"${m['long_pnl']:>+11,.0f} ${m['short_pnl']:>+11,.0f}\n")
        f.write("-" * 120 + "\n\n")
        # Per-symbol breakdown for each NOLOSS level
        for noloss in sorted(NOLOSS_LEVELS):
            f.write(f"\nNOLOSS = {noloss}% — Per-Symbol Breakdown\n")
            f.write("-" * 100 + "\n")
            f.write(f"{'Symbol':>8s} {'Trades':>7s} {'TotalPnL':>12s} {'AvgPnL%':>9s} {'WR%':>7s} "
                    f"{'PF':>7s} {'Sharpe':>8s} {'MaxDD%':>8s}\n")
            f.write("-" * 100 + "\n")
            for s in SYMBOLS:
                m = results[noloss]["per_symbol"][s]
                f.write(f"{s:>8s} {m['n_trades']:>7d} ${m['total_pnl']:>+11,.0f} "
                        f"{m['avg_pnl_pct']:>+8.3f}% {m['win_rate']:>6.1f}% "
                        f"{m['profit_factor']:>6.2f} {m['weekly_sharpe']:>+7.2f} "
                        f"{m['max_dd_trade']:>+7.2f}%\n")
            f.write("\n")
        # Analysis
        f.write("\n" + "=" * 120 + "\n")
        f.write("ANALYSIS\n")
        f.write("=" * 120 + "\n\n")
        best_sharpe = max(NOLOSS_LEVELS, key=lambda x: results[x]["total"]["weekly_sharpe"])
        best_pnl = max(NOLOSS_LEVELS, key=lambda x: results[x]["total"]["total_pnl"])
        best_wr = max(NOLOSS_LEVELS, key=lambda x: results[x]["total"]["win_rate"])
        best_pf = max(NOLOSS_LEVELS, key=lambda x: results[x]["total"]["profit_factor"])
        f.write(f"Best Weekly Sharpe: NOLOSS={best_sharpe}% -> {results[best_sharpe]['total']['weekly_sharpe']:+.2f}\n")
        f.write(f"Best Total PnL:    NOLOSS={best_pnl}% -> ${results[best_pnl]['total']['total_pnl']:+,.0f}\n")
        f.write(f"Best Win Rate:     NOLOSS={best_wr}% -> {results[best_wr]['total']['win_rate']:.1f}%\n")
        f.write(f"Best Profit Factor: NOLOSS={best_pf}% -> {results[best_pf]['total']['profit_factor']:.2f}\n\n")
        # Compare current (0.3%) vs alternatives
        curr = results[0.3]["total"]
        f.write(f"Current setting (0.3%) baseline:\n")
        f.write(f"  Trades={curr['n_trades']}, PnL=${curr['total_pnl']:+,.0f}, "
                f"WR={curr['win_rate']:.1f}%, PF={curr['profit_factor']:.2f}, "
                f"Sharpe={curr['weekly_sharpe']:+.2f}\n\n")
        for noloss in sorted(NOLOSS_LEVELS):
            if noloss == 0.3:
                continue
            m = results[noloss]["total"]
            pnl_diff = m["total_pnl"] - curr["total_pnl"]
            sharpe_diff = m["weekly_sharpe"] - curr["weekly_sharpe"]
            f.write(f"  vs {noloss:+.1f}%: PnL {'+' if pnl_diff >= 0 else ''}{pnl_diff:,.0f}, "
                    f"Sharpe {'+' if sharpe_diff >= 0 else ''}{sharpe_diff:.2f}, "
                    f"Trades={m['n_trades']} ({m['n_trades'] - curr['n_trades']:+d}), "
                    f"WR={m['win_rate']:.1f}%\n")
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    main()
