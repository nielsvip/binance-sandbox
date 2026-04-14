#!/usr/bin/env python3
"""
Cross-Exit Test: Run each exit system on BOTH markets, plus hybrids.
20 tests total. Results to cross_exit_test_results.txt.
"""

import numpy as np
import os
import sys
import time
import datetime
from collections import defaultdict

sys.path.insert(0, "/Users/niels/Documents/binance")
from wt_dc_entry_scorer import score_entry
from wt_dc_exit_scorer import score_exit
from wt_dc_delta import DeltaTracker

NPZ_DIR = "/Users/niels/Documents/binance/backtest_v8/indicators"
RESULTS_FILE = "/Users/niels/Documents/binance/cross_exit_test_results.txt"

# Symbols
STOCKS = ["MU", "AAPL", "TTD", "FIVN", "AMZN", "MRVL", "XOM", "CVX", "GLD", "USO", "NVDA", "MSFT", "ASTS"]
CRYPTO = ["ANKRUSDT", "ATOMUSDT", "BANDUSDT", "BATUSDT", "BELUSDT",
          "BTCDOMUSDT", "CELRUSDT", "CHRUSDT"]  # 8 valid, COMPUSDT corrupt, BTCUSDT too short

# Sizing
STOCK_CAPITAL = 180_000.0
CRYPTO_CAPITAL_PER = 200.0  # $200 x 20x leverage = $4000 notional
CRYPTO_LEVERAGE = 20

# Trading hours for stocks (UTC)
MARKET_OPEN_H, MARKET_OPEN_M = 13, 30
MARKET_CLOSE_H, MARKET_CLOSE_M = 20, 0

# Max hold bars before force exit
MAX_HOLD_BARS = 96

# Entry threshold
ENTRY_THRESHOLD = 37

# TF weights for delta engine
STOCK_TF_WEIGHTS = {"5m": 0.30, "15m": 0.25, "1h": 0.20, "4h": 0.15, "D": 0.10}
CRYPTO_TF_WEIGHTS = {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5}


def load_npz(symbol):
    """Load NPZ and return dict of arrays."""
    path = os.path.join(NPZ_DIR, f"{symbol}.npz")
    f = np.load(path, allow_pickle=True)
    return {k: f[k] for k in f.files}


def is_trading_hours(ts):
    """Check if timestamp is within stock trading hours (weekday 13:30-20:00 UTC)."""
    dt = datetime.datetime.utcfromtimestamp(ts)
    if dt.weekday() >= 5:  # Sat/Sun
        return False
    t = dt.hour * 60 + dt.minute
    return (MARKET_OPEN_H * 60 + MARKET_OPEN_M) <= t < (MARKET_CLOSE_H * 60 + MARKET_CLOSE_M)


def get_indicators_at(data, idx, keys):
    """Extract indicator dict at bar index."""
    return {k: float(data[k][idx]) for k in keys}


def compute_pnl(entry_price, exit_price, is_long, size_usd):
    """Compute PnL for a trade."""
    if is_long:
        return size_usd * (exit_price - entry_price) / entry_price
    else:
        return size_usd * (entry_price - exit_price) / entry_price


def weekly_sharpe(trades):
    """Compute per-symbol weekly Sharpe from trade list."""
    if not trades:
        return 0.0
    # Group trades by week
    weekly_pnl = defaultdict(float)
    for t in trades:
        week = datetime.datetime.utcfromtimestamp(t["exit_ts"]).isocalendar()[:2]
        weekly_pnl[week] += t["pnl"]
    if len(weekly_pnl) < 2:
        return 0.0
    vals = list(weekly_pnl.values())
    mean = np.mean(vals)
    std = np.std(vals, ddof=1)
    if std < 1e-10:
        return 0.0
    return mean / std


def run_backtest(symbol, data, is_stock, exit_fn, step=2, start_ts=None):
    """
    Run backtest for one symbol with given exit function.
    exit_fn(indicators, is_long, hold_bars, delta_tracker, symbol) -> bool
    Returns list of trades.
    """
    keys = list(data.keys())
    timestamps = data["timestamps"]
    closes = data["close"]
    n = len(closes)

    # Find start index
    if start_ts:
        start_idx = int(np.searchsorted(timestamps, start_ts))
    else:
        start_idx = 500  # warmup

    # Size per trade
    if is_stock:
        size_usd = STOCK_CAPITAL / len(STOCKS)
    else:
        size_usd = CRYPTO_CAPITAL_PER * CRYPTO_LEVERAGE

    trades = []
    position = None  # {is_long, entry_price, entry_bar, entry_ts}

    for i in range(start_idx, n, step):
        ts = float(timestamps[i])
        price = float(closes[i])

        # Stock trading hours filter
        if is_stock and not is_trading_hours(ts):
            continue

        if price <= 0 or np.isnan(price):
            continue

        ind = get_indicators_at(data, i, keys)

        if position is None:
            # Try entry - check both long and short
            score_l, _ = score_entry(ind, is_long=True)
            score_s, _ = score_entry(ind, is_long=False)

            if score_l >= ENTRY_THRESHOLD and score_l >= score_s:
                position = {"is_long": True, "entry_price": price, "entry_bar": i, "entry_ts": ts, "hold_bars": 0}
            elif score_s >= ENTRY_THRESHOLD:
                position = {"is_long": False, "entry_price": price, "entry_bar": i, "entry_ts": ts, "hold_bars": 0}
        else:
            position["hold_bars"] += 1
            is_long = position["is_long"]
            hold_bars = position["hold_bars"]

            should_exit = exit_fn(ind, is_long, hold_bars, symbol)
            force_exit = hold_bars >= MAX_HOLD_BARS

            if should_exit or force_exit:
                pnl = compute_pnl(position["entry_price"], price, is_long, size_usd)
                trades.append({
                    "symbol": symbol,
                    "is_long": is_long,
                    "entry_price": position["entry_price"],
                    "exit_price": price,
                    "entry_ts": position["entry_ts"],
                    "exit_ts": ts,
                    "hold_bars": hold_bars,
                    "pnl": pnl,
                    "forced": force_exit and not should_exit,
                })
                position = None

    return trades


def make_scorer_exit(threshold):
    """Create exit function using score_exit with given threshold."""
    def exit_fn(indicators, is_long, hold_bars, symbol):
        sc, _ = score_exit(indicators, is_long)
        return sc >= threshold
    return exit_fn


def make_delta_exit(cfg, is_stock):
    """Create exit function using DeltaTracker."""
    trackers = {}  # per-symbol tracker

    def exit_fn(indicators, is_long, hold_bars, symbol):
        if symbol not in trackers:
            trackers[symbol] = DeltaTracker(cfg)
            trackers[symbol]._max_speed[symbol] = 0.0

        tracker = trackers[symbol]
        side = "LONG" if is_long else "SHORT"
        pos_state = {"side": side, "max_speed": tracker._max_speed.get(symbol, 0), "n_entries": 1, "last_entry_price": 0}
        sig = tracker.update(symbol, indicators, pos_state)

        if is_long:
            return sig.exit_long
        else:
            return sig.exit_short
    return exit_fn


def make_delta_update_exit(cfg, is_stock):
    """Delta exit that also updates tracker on every bar (including no-position bars).
    We need the tracker to build history, so we update every bar in the main loop.
    This version uses a shared tracker dict."""
    trackers = {}
    in_position = {}  # symbol -> bool

    def register_bar(symbol, indicators, is_long, has_position):
        """Called every bar to update tracker state."""
        if symbol not in trackers:
            trackers[symbol] = DeltaTracker(cfg)

        tracker = trackers[symbol]
        if has_position:
            side = "LONG" if is_long else "SHORT"
            pos_state = {"side": side, "max_speed": tracker._max_speed.get(symbol, 0), "n_entries": 1, "last_entry_price": 0}
        else:
            pos_state = None
            # Reset max speed when not in position
            tracker._max_speed[symbol] = 0.0

        sig = tracker.update(symbol, indicators, pos_state)
        return sig

    def exit_fn(indicators, is_long, hold_bars, symbol):
        sig = register_bar(symbol, indicators, is_long, True)
        if is_long:
            return sig.exit_long
        else:
            return sig.exit_short

    return exit_fn, register_bar


def make_hybrid_exit(option, cfg, is_stock):
    """Create hybrid exit function combining scorer + delta."""
    trackers = {}

    def exit_fn(indicators, is_long, hold_bars, symbol):
        # Scorer
        sc, _ = score_exit(indicators, is_long)

        # Delta
        if symbol not in trackers:
            trackers[symbol] = DeltaTracker(cfg)
        tracker = trackers[symbol]
        side = "LONG" if is_long else "SHORT"
        pos_state = {"side": side, "max_speed": tracker._max_speed.get(symbol, 0), "n_entries": 1, "last_entry_price": 0}
        sig = tracker.update(symbol, indicators, pos_state)
        delta_exit = sig.exit_long if is_long else sig.exit_short

        if option == 1:
            # Conservative: BOTH must agree
            return sc >= 30 and delta_exit
        elif option == 2:
            # Either high-confidence
            return sc >= 40 or delta_exit
        elif option == 3:
            # Scorer gate + delta boost
            return sc >= 25 and (delta_exit or sc >= 40)
        return False

    return exit_fn


def run_backtest_with_delta_updates(symbol, data, is_stock, exit_fn, register_bar, step=2, start_ts=None):
    """Backtest that calls register_bar on EVERY bar for delta history building."""
    keys = list(data.keys())
    timestamps = data["timestamps"]
    closes = data["close"]
    n = len(closes)

    if start_ts:
        start_idx = int(np.searchsorted(timestamps, start_ts))
    else:
        start_idx = 500

    if is_stock:
        size_usd = STOCK_CAPITAL / len(STOCKS)
    else:
        size_usd = CRYPTO_CAPITAL_PER * CRYPTO_LEVERAGE

    trades = []
    position = None

    for i in range(start_idx, n, step):
        ts = float(timestamps[i])
        price = float(closes[i])

        if is_stock and not is_trading_hours(ts):
            continue
        if price <= 0 or np.isnan(price):
            continue

        ind = get_indicators_at(data, i, keys)

        if position is None:
            # Update delta tracker even without position
            register_bar(symbol, ind, True, False)

            score_l, _ = score_entry(ind, is_long=True)
            score_s, _ = score_entry(ind, is_long=False)
            if score_l >= ENTRY_THRESHOLD and score_l >= score_s:
                position = {"is_long": True, "entry_price": price, "entry_bar": i, "entry_ts": ts, "hold_bars": 0}
            elif score_s >= ENTRY_THRESHOLD:
                position = {"is_long": False, "entry_price": price, "entry_bar": i, "entry_ts": ts, "hold_bars": 0}
        else:
            position["hold_bars"] += 1
            is_long = position["is_long"]
            hold_bars = position["hold_bars"]

            should_exit = exit_fn(ind, is_long, hold_bars, symbol)
            force_exit = hold_bars >= MAX_HOLD_BARS

            if should_exit or force_exit:
                pnl = compute_pnl(position["entry_price"], price, is_long, size_usd)
                trades.append({
                    "symbol": symbol,
                    "is_long": is_long,
                    "entry_price": position["entry_price"],
                    "exit_price": price,
                    "entry_ts": position["entry_ts"],
                    "exit_ts": ts,
                    "hold_bars": hold_bars,
                    "pnl": pnl,
                    "forced": force_exit and not should_exit,
                })
                position = None
                # Reset tracker max speed
                if symbol in register_bar.__code__.co_freevars:
                    pass  # handled inside register_bar

    return trades


def compute_stats(trades):
    """Compute summary stats from trade list."""
    if not trades:
        return {"trades": 0, "wr": 0, "pf": 0, "sharpe": 0, "avg_hold": 0, "total_pnl": 0}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_win = sum(t["pnl"] for t in wins) if wins else 0
    total_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0.001
    wr = len(wins) / len(trades) * 100
    pf = total_win / total_loss if total_loss > 0 else 999.0
    avg_hold = np.mean([t["hold_bars"] for t in trades])
    sharpe = weekly_sharpe(trades)
    total_pnl = sum(t["pnl"] for t in trades)

    return {
        "trades": len(trades),
        "wr": wr,
        "pf": pf,
        "sharpe": sharpe,
        "avg_hold": avg_hold,
        "total_pnl": total_pnl,
    }


def format_row(system, market, config_str, stats):
    """Format one result row."""
    return f"{system:<16s} {market:<8s} {config_str:<28s} {stats['trades']:>6d}   {stats['wr']:>5.1f}%  {stats['pf']:>5.2f}x  {stats['sharpe']:>7.2f}  {stats['avg_hold']:>6.1f}  ${stats['total_pnl']:>10.0f}"


def main():
    results = []
    header = f"{'System':<16s} {'Market':<8s} {'Config':<28s} {'Trades':>6s}   {'WR':>6s}  {'PF':>6s}  {'WkShrp':>7s}  {'AvgHld':>6s}  {'TotalPnL':>11s}"
    sep = "-" * len(header)

    print("=" * 80)
    print("CROSS-EXIT TEST: Scorer vs Delta vs Hybrid on Stocks AND Crypto")
    print("=" * 80)
    print()

    # Pre-load all data
    print("Loading stock data...")
    stock_data = {}
    for sym in STOCKS:
        stock_data[sym] = load_npz(sym)
        print(f"  {sym}: {len(stock_data[sym]['close'])} bars")

    print("Loading crypto data...")
    crypto_data = {}
    for sym in CRYPTO:
        crypto_data[sym] = load_npz(sym)
        print(f"  {sym}: {len(crypto_data[sym]['close'])} bars")

    # Start timestamps: stocks from 2024-04-01, crypto from 2024-01-01
    stock_start = int(datetime.datetime(2024, 4, 1, tzinfo=datetime.timezone.utc).timestamp())
    crypto_start = int(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp())

    print()
    print(header)
    print(sep)

    # ====================================================================
    # TEST 1-4: Scorer on stocks, thresholds [25, 30, 35, 40]
    # ====================================================================
    for thresh in [25, 30, 35, 40]:
        t0 = time.time()
        all_trades = []
        exit_fn = make_scorer_exit(thresh)
        for sym in STOCKS:
            trades = run_backtest(sym, stock_data[sym], is_stock=True, exit_fn=exit_fn, step=2, start_ts=stock_start)
            all_trades.extend(trades)
        stats = compute_stats(all_trades)
        row = format_row("Scorer", "STOCK", f"thresh={thresh}", stats)
        print(row)
        results.append(row)
        elapsed = time.time() - t0
        sys.stdout.flush()

    # ====================================================================
    # TEST 5-8: Scorer on crypto, thresholds [25, 30, 35, 40]
    # ====================================================================
    for thresh in [25, 30, 35, 40]:
        t0 = time.time()
        all_trades = []
        exit_fn = make_scorer_exit(thresh)
        for sym in CRYPTO:
            trades = run_backtest(sym, crypto_data[sym], is_stock=False, exit_fn=exit_fn, step=2, start_ts=crypto_start)
            all_trades.extend(trades)
        stats = compute_stats(all_trades)
        row = format_row("Scorer", "CRYPTO", f"thresh={thresh}", stats)
        print(row)
        results.append(row)
        sys.stdout.flush()

    # ====================================================================
    # TEST 9-11: Delta on stocks, 3 configs
    # ====================================================================
    delta_stock_configs = [
        {"entry_min_tf": 2, "exit_speed_decay_pct": 50, "exit_min_tf_lost": 2, "tf_weights": STOCK_TF_WEIGHTS, "entry_speed_threshold": 0.5, "exit_min_hold": 4, "pyramid_max": 0},
        {"entry_min_tf": 3, "exit_speed_decay_pct": 30, "exit_min_tf_lost": 1, "tf_weights": STOCK_TF_WEIGHTS, "entry_speed_threshold": 0.5, "exit_min_hold": 4, "pyramid_max": 0},
        {"entry_min_tf": 4, "exit_speed_decay_pct": 70, "exit_min_tf_lost": 3, "tf_weights": STOCK_TF_WEIGHTS, "entry_speed_threshold": 0.5, "exit_min_hold": 4, "pyramid_max": 0},
    ]
    delta_stock_labels = [
        "tf=2,decay=50,lost=2",
        "tf=3,decay=30,lost=1",
        "tf=4,decay=70,lost=3",
    ]
    for cfg, label in zip(delta_stock_configs, delta_stock_labels):
        t0 = time.time()
        all_trades = []
        exit_fn, register_bar = make_delta_update_exit(cfg, is_stock=True)
        for sym in STOCKS:
            trades = run_backtest_with_delta_updates(sym, stock_data[sym], is_stock=True, exit_fn=exit_fn, register_bar=register_bar, step=2, start_ts=stock_start)
            all_trades.extend(trades)
        stats = compute_stats(all_trades)
        row = format_row("Delta", "STOCK", label, stats)
        print(row)
        results.append(row)
        sys.stdout.flush()

    # ====================================================================
    # TEST 12-14: Delta on crypto, 3 configs
    # ====================================================================
    delta_crypto_configs = [
        {"entry_min_tf": 2, "exit_speed_decay_pct": 50, "exit_min_tf_lost": 2, "tf_weights": CRYPTO_TF_WEIGHTS, "entry_speed_threshold": 0.5, "exit_min_hold": 4, "pyramid_max": 0},
        {"entry_min_tf": 3, "exit_speed_decay_pct": 30, "exit_min_tf_lost": 1, "tf_weights": CRYPTO_TF_WEIGHTS, "entry_speed_threshold": 0.5, "exit_min_hold": 4, "pyramid_max": 0},
        {"entry_min_tf": 4, "exit_speed_decay_pct": 70, "exit_min_tf_lost": 3, "tf_weights": CRYPTO_TF_WEIGHTS, "entry_speed_threshold": 0.5, "exit_min_hold": 4, "pyramid_max": 0},
    ]
    delta_crypto_labels = [
        "tf=2,decay=50,lost=2",
        "tf=3,decay=30,lost=1",
        "tf=4,decay=70,lost=3",
    ]
    for cfg, label in zip(delta_crypto_configs, delta_crypto_labels):
        t0 = time.time()
        all_trades = []
        exit_fn, register_bar = make_delta_update_exit(cfg, is_stock=False)
        for sym in CRYPTO:
            trades = run_backtest_with_delta_updates(sym, crypto_data[sym], is_stock=False, exit_fn=exit_fn, register_bar=register_bar, step=2, start_ts=crypto_start)
            all_trades.extend(trades)
        stats = compute_stats(all_trades)
        row = format_row("Delta", "CRYPTO", label, stats)
        print(row)
        results.append(row)
        sys.stdout.flush()

    # ====================================================================
    # TEST 15-17: Hybrid on stocks, 3 options
    # ====================================================================
    hybrid_cfg_stock = {"entry_min_tf": 2, "exit_speed_decay_pct": 50, "exit_min_tf_lost": 2, "tf_weights": STOCK_TF_WEIGHTS, "entry_speed_threshold": 0.5, "exit_min_hold": 4, "pyramid_max": 0}
    hybrid_labels = [
        "BOTH: s>=30 AND delta",
        "EITHER: s>=40 OR delta",
        "GATE: s>=25 AND (d OR s>=40)",
    ]
    for opt, label in zip([1, 2, 3], hybrid_labels):
        t0 = time.time()
        all_trades = []
        exit_fn = make_hybrid_exit(opt, hybrid_cfg_stock, is_stock=True)
        for sym in STOCKS:
            # Hybrid uses delta internally, needs bar-by-bar update
            # We'll use the simple run_backtest since the hybrid exit_fn calls tracker.update itself
            trades = run_backtest(sym, stock_data[sym], is_stock=True, exit_fn=exit_fn, step=2, start_ts=stock_start)
            all_trades.extend(trades)
        stats = compute_stats(all_trades)
        sys_name = f"Hybrid_opt{opt}"
        row = format_row(sys_name, "STOCK", label, stats)
        print(row)
        results.append(row)
        sys.stdout.flush()

    # ====================================================================
    # TEST 18-20: Hybrid on crypto, 3 options
    # ====================================================================
    hybrid_cfg_crypto = {"entry_min_tf": 2, "exit_speed_decay_pct": 50, "exit_min_tf_lost": 2, "tf_weights": CRYPTO_TF_WEIGHTS, "entry_speed_threshold": 0.5, "exit_min_hold": 4, "pyramid_max": 0}
    for opt, label in zip([1, 2, 3], hybrid_labels):
        t0 = time.time()
        all_trades = []
        exit_fn = make_hybrid_exit(opt, hybrid_cfg_crypto, is_stock=False)
        for sym in CRYPTO:
            trades = run_backtest(sym, crypto_data[sym], is_stock=False, exit_fn=exit_fn, step=2, start_ts=crypto_start)
            all_trades.extend(trades)
        stats = compute_stats(all_trades)
        sys_name = f"Hybrid_opt{opt}"
        row = format_row(sys_name, "CRYPTO", label, stats)
        print(row)
        results.append(row)
        sys.stdout.flush()

    # Write results file
    print()
    print("=" * 80)
    print("WRITING RESULTS TO", RESULTS_FILE)
    print("=" * 80)

    with open(RESULTS_FILE, "w") as f:
        f.write("CROSS-EXIT TEST RESULTS\n")
        f.write(f"Generated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")
        f.write(f"Stocks: {', '.join(STOCKS)} ({len(STOCKS)} symbols, from 2024-04-01)\n")
        f.write(f"Crypto: {', '.join(CRYPTO)} ({len(CRYPTO)} symbols, from 2024-01-01)\n")
        f.write(f"Entry: score_entry >= {ENTRY_THRESHOLD}\n")
        f.write(f"Step: every 2nd bar | Max hold: {MAX_HOLD_BARS} bars\n")
        f.write(f"Stock sizing: ${STOCK_CAPITAL:,.0f} / {len(STOCKS)} = ${STOCK_CAPITAL/len(STOCKS):,.0f} per symbol\n")
        f.write(f"Crypto sizing: ${CRYPTO_CAPITAL_PER} x {CRYPTO_LEVERAGE}x = ${CRYPTO_CAPITAL_PER*CRYPTO_LEVERAGE:,.0f} notional per trade\n")
        f.write("\n")
        f.write(header + "\n")
        f.write(sep + "\n")
        for r in results:
            f.write(r + "\n")
        f.write("\n")
        f.write("LEGEND:\n")
        f.write("  Scorer  = wt_dc_exit_scorer.score_exit() >= threshold\n")
        f.write("  Delta   = DeltaTracker.update() exit_long/exit_short signal\n")
        f.write("  Hybrid_opt1 = BOTH scorer>=30 AND delta exit (conservative)\n")
        f.write("  Hybrid_opt2 = scorer>=40 OR delta exit (either high-confidence)\n")
        f.write("  Hybrid_opt3 = scorer>=25 AND (delta exit OR scorer>=40) (gate+boost)\n")
        f.write("  WkShrp  = per-symbol weekly Sharpe ratio\n")
        f.write("  AvgHld  = average hold time in bars (stock=5min bars, crypto=15min bars)\n")
        f.write("  TotalPnL = sum of all trade PnL in USD\n")

    print("\nDone! Results written to", RESULTS_FILE)


if __name__ == "__main__":
    main()
