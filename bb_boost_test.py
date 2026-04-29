"""
BB 2.5σ Breakout + WT/DC Scorer Backtest (Vectorized)
=====================================================
Pre-computes entry/exit scores as numpy arrays, then runs fast position sim.
"""

import numpy as np
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/Users/niels/Documents/binance")
from wt_dc_entry_scorer import score_entry as _score_entry_fn
from wt_dc_exit_scorer import score_exit as _score_exit_fn

NPZ_DIR = "/Users/niels/Documents/binance/backtest_v8/indicators"
SYMBOLS = ["MU", "AAPL", "TTD", "FIVN", "AMZN", "MRVL", "XOM", "CVX", "GLD", "USO", "NVDA", "MSFT", "ASTS"]

CAPITAL = 180_000.0
BASE_PCT = 0.02
BASE_SIZE = CAPITAL * BASE_PCT  # $3,600

ENTRY_THRESHOLD = 37
EXIT_THRESHOLD = 20
START_TS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())

BB_SANE_MIN = -5.0
BB_SANE_MAX = 5.0


def vectorized_scores(data, keys, n):
    """Pre-compute entry and exit scores for all bars using vectorized approach.
    Calls the real scorer functions but only on every 3rd bar (5m resolution ~ 15min effective)
    to keep runtime manageable. Interpolates between."""
    entry_long = np.full(n, 0.0)
    entry_short = np.full(n, 0.0)
    exit_long = np.full(n, 0.0)
    exit_short = np.full(n, 0.0)

    # Sample every 3 bars (every 15 min at 5m resolution)
    STEP = 3
    sample_indices = list(range(500, n, STEP))

    for idx in sample_indices:
        ind = {k: float(data[k][idx]) for k in keys}
        el, _ = _score_entry_fn(ind, is_long=True)
        es, _ = _score_entry_fn(ind, is_long=False)
        xl, _ = _score_exit_fn(ind, is_long=True)
        xs, _ = _score_exit_fn(ind, is_long=False)
        entry_long[idx] = el
        entry_short[idx] = es
        exit_long[idx] = xl
        exit_short[idx] = xs

    # Forward-fill to cover skipped bars
    for i in range(501, n):
        if entry_long[i] == 0.0 and i not in set(sample_indices):
            entry_long[i] = entry_long[i - 1]
            entry_short[i] = entry_short[i - 1]
            exit_long[i] = exit_long[i - 1]
            exit_short[i] = exit_short[i - 1]

    return entry_long, entry_short, exit_long, exit_short


def compute_trading_mask(timestamps):
    """Boolean mask: True if bar is during trading hours."""
    n = len(timestamps)
    mask = np.zeros(n, dtype=bool)
    for i in range(n):
        ts = int(timestamps[i])
        if ts < START_TS:
            continue
        dt = datetime.utcfromtimestamp(ts)
        if dt.weekday() >= 5:
            continue
        sod = dt.hour * 3600 + dt.minute * 60
        if 48600 <= sod < 72000:  # 13:30 = 48600, 20:00 = 72000
            mask[i] = True
    return mask


def run_all_configs(symbol, data, entry_long, entry_short, exit_long, exit_short, trading_mask):
    """Run all 5 configs for one symbol. Returns dict of config -> trades list."""
    timestamps = data["timestamps"].astype(np.int64)
    close = data["close"].astype(np.float64)
    bb_4h = data.get("bb_pct_b_4h", np.full(len(close), 0.5)).astype(np.float64)
    n = len(close)

    # Pre-compute sane BB mask
    bb_sane = (bb_4h > BB_SANE_MIN) & (bb_4h < BB_SANE_MAX) & ~np.isnan(bb_4h)

    configs = ["BASELINE", "BB_BOOST", "BB_BOOST_25", "BB_ONLY", "BB_RETEST"]
    all_results = {}

    for config in configs:
        trades = []
        position = None

        for i in range(500, n):
            if not trading_mask[i]:
                continue

            price = close[i]
            if price <= 0 or np.isnan(price):
                continue

            bb_val = bb_4h[i] if bb_sane[i] else 0.5
            bb_ok = bb_sane[i]

            # === EXIT CHECK ===
            if position is not None:
                is_long = position["is_long"]
                ex_score = exit_long[i] if is_long else exit_short[i]

                if ex_score >= EXIT_THRESHOLD:
                    entry_price = position["entry_price"]
                    size_dollars = position["size_dollars"]
                    if is_long:
                        pnl_pct = (price - entry_price) / entry_price
                    else:
                        pnl_pct = (entry_price - price) / entry_price
                    pnl_dollars = pnl_pct * size_dollars
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": int(timestamps[i]),
                        "pnl": pnl_dollars,
                        "size_mult": position["size_mult"],
                        "is_long": is_long,
                        "entry_score": position["entry_score"],
                        "bb_at_entry": position["bb_at_entry"],
                        "entry_price": entry_price,
                        "exit_price": price,
                    })
                    position = None
                    continue

                # BB_RETEST add logic
                if config == "BB_RETEST" and position.get("had_bb_breakout") and not position.get("retest_added"):
                    if bb_ok and 0.85 <= bb_val <= 1.05:
                        old_size = position["size_dollars"]
                        add_size = BASE_SIZE * 1.5
                        total_size = old_size + add_size
                        old_entry = position["entry_price"]
                        new_entry = (old_entry * old_size + price * add_size) / total_size
                        position["entry_price"] = new_entry
                        position["size_dollars"] = total_size
                        position["size_mult"] = total_size / BASE_SIZE
                        position["retest_added"] = True

            # === ENTRY CHECK ===
            if position is None:
                el_score = entry_long[i]
                es_score = entry_short[i]

                is_long = el_score >= es_score
                entry_score = el_score if is_long else es_score

                if entry_score < ENTRY_THRESHOLD:
                    continue

                size_mult = 1.0
                should_enter = True
                had_bb_breakout = False

                if config == "BASELINE":
                    size_mult = 1.0

                elif config == "BB_BOOST":
                    if bb_ok and bb_val > 1.0:
                        size_mult = 2.0
                        had_bb_breakout = True

                elif config == "BB_BOOST_25":
                    if bb_ok and bb_val > 1.125:
                        size_mult = 2.0
                        had_bb_breakout = True

                elif config == "BB_ONLY":
                    if not (bb_ok and bb_val > 1.0):
                        should_enter = False
                    else:
                        had_bb_breakout = True

                elif config == "BB_RETEST":
                    if bb_ok and bb_val > 1.0:
                        had_bb_breakout = True

                if not should_enter:
                    continue

                size_dollars = BASE_SIZE * size_mult

                position = {
                    "entry_price": price,
                    "entry_ts": int(timestamps[i]),
                    "is_long": is_long,
                    "size_dollars": size_dollars,
                    "size_mult": size_mult,
                    "entry_score": entry_score,
                    "bb_at_entry": bb_val,
                    "had_bb_breakout": had_bb_breakout,
                    "retest_added": False,
                }

        # Close remaining position
        if position is not None:
            price = close[-1]
            is_long = position["is_long"]
            entry_price = position["entry_price"]
            size_dollars = position["size_dollars"]
            if is_long:
                pnl_pct = (price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - price) / entry_price
            trades.append({
                "entry_ts": position["entry_ts"],
                "exit_ts": int(timestamps[-1]),
                "pnl": pnl_pct * size_dollars,
                "size_mult": position["size_mult"],
                "is_long": is_long,
                "entry_score": position["entry_score"],
                "bb_at_entry": position["bb_at_entry"],
                "entry_price": entry_price,
                "exit_price": price,
            })

        all_results[config] = trades

    return all_results


def compute_weekly_sharpe(trades):
    """Per-trade pool_sharpe (function name kept for back-compat).
    Weekly bucketing + sqrt(52) annualization stripped 2026-04-29 per CLAUDE.md rule 4.
    Pool Sharpe = mean(per-trade pnl) / std(per-trade pnl), unitless, frequency-blind."""
    if not trades:
        return 0.0
    pnls = np.array([t.get("pnl_pct", t.get("pnl", 0.0)) for t in trades])
    if len(pnls) < 2:
        return 0.0
    std = np.std(pnls)
    if std == 0:
        return 0.0
    return float(np.mean(pnls) / std)


def max_drawdown_dollars(trades):
    """Max drawdown from cumulative PnL."""
    if not trades:
        return 0.0
    cum = np.cumsum([t["pnl"] for t in sorted(trades, key=lambda x: x["exit_ts"])])
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    return np.max(dd) if len(dd) > 0 else 0.0


def main():
    configs = ["BASELINE", "BB_BOOST", "BB_BOOST_25", "BB_ONLY", "BB_RETEST"]
    results = {c: {"all_trades": [], "symbol_results": {}} for c in configs}

    print(f"BB Boost + WT/DC Scorer Backtest (Vectorized)")
    print(f"Capital: ${CAPITAL:,.0f}, Base size: ${BASE_SIZE:,.0f} (2%)")
    print(f"Entry >= {ENTRY_THRESHOLD}, Exit >= {EXIT_THRESHOLD}")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Period: 2024-01-01+, 13:30-20:00 UTC weekdays")
    print("=" * 100)

    total_t0 = time.time()

    for symbol in SYMBOLS:
        t0 = time.time()
        print(f"\nLoading {symbol}...", end=" ", flush=True)
        data = {}
        f = np.load(os.path.join(NPZ_DIR, f"{symbol}.npz"), allow_pickle=True)
        for k in f.files:
            arr = f[k]
            if arr.dtype.kind in ('f', 'i', 'u'):
                data[k] = arr.astype(np.float64)
            else:
                data[k] = arr
        n = len(data["close"])
        print(f"{n} bars.", end=" ", flush=True)

        # Pre-compute trading mask
        trading_mask = compute_trading_mask(data["timestamps"])
        trading_bars = np.sum(trading_mask)
        print(f"{trading_bars} trading bars.", end=" ", flush=True)

        # Pre-compute scores
        indicator_keys = [k for k in data.keys() if k != "timestamps"]
        entry_long, entry_short, exit_long, exit_short = vectorized_scores(data, indicator_keys, n)
        t1 = time.time()
        print(f"Scores in {t1-t0:.1f}s.", end=" ", flush=True)

        # Run all configs
        config_trades = run_all_configs(symbol, data, entry_long, entry_short, exit_long, exit_short, trading_mask)
        t2 = time.time()
        print(f"Sim in {t2-t1:.1f}s.")

        for config in configs:
            trades = config_trades[config]
            total_pnl = sum(t["pnl"] for t in trades)
            winners = [t for t in trades if t["pnl"] > 0]
            wr = len(winners) / len(trades) * 100 if trades else 0
            sharpe = compute_weekly_sharpe(trades)
            avg_hold = np.mean([(t["exit_ts"] - t["entry_ts"]) / 3600 for t in trades]) if trades else 0
            bb_boosted = [t for t in trades if t["size_mult"] > 1.0]
            bb_pnl = sum(t["pnl"] for t in bb_boosted)

            results[config]["all_trades"].extend(trades)
            results[config]["symbol_results"][symbol] = {
                "trades": len(trades),
                "pnl": total_pnl,
                "wr": wr,
                "sharpe": sharpe,
                "avg_hold_hrs": avg_hold,
                "bb_boosted": len(bb_boosted),
                "bb_boosted_pnl": bb_pnl,
                "winners": len(winners),
                "losers": len(trades) - len(winners),
                "max_dd": max_drawdown_dollars(trades),
            }

            print(f"  {config:<14} {symbol}: {len(trades):4d} trades, PnL=${total_pnl:+10,.2f}, "
                  f"WR={wr:5.1f}%, Sharpe={sharpe:+6.2f}, BB={len(bb_boosted)}")

    total_elapsed = time.time() - total_t0
    print(f"\nTotal runtime: {total_elapsed:.1f}s")

    # === Aggregate ===
    print("\n" + "=" * 100)
    print("AGGREGATE RESULTS")
    print("=" * 100)
    print(f"{'Config':<16} {'Trades':>7} {'PnL':>14} {'WR%':>7} {'Sharpe':>8} {'MaxDD':>12} {'BB#':>6} {'BB_PnL':>12}")
    print("-" * 100)

    for config in configs:
        at = results[config]["all_trades"]
        total_pnl = sum(t["pnl"] for t in at)
        total_trades = len(at)
        total_winners = sum(1 for t in at if t["pnl"] > 0)
        total_wr = total_winners / total_trades * 100 if total_trades else 0
        agg_sharpe = compute_weekly_sharpe(at)
        mdd = max_drawdown_dollars(at)
        bb_count = sum(sr["bb_boosted"] for sr in results[config]["symbol_results"].values())
        bb_pnl = sum(sr["bb_boosted_pnl"] for sr in results[config]["symbol_results"].values())

        results[config]["agg"] = {
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "total_wr": total_wr,
            "agg_sharpe": agg_sharpe,
            "max_dd": mdd,
            "bb_count": bb_count,
            "bb_pnl": bb_pnl,
        }

        print(f"{config:<16} {total_trades:>7} ${total_pnl:>+12,.2f} {total_wr:>6.1f}% "
              f"{agg_sharpe:>+7.2f} ${mdd:>10,.2f} {bb_count:>6} ${bb_pnl:>+10,.0f}")

    # === Write results file ===
    output_path = "/Users/niels/Documents/binance/bb_boost_test_results.txt"
    with open(output_path, "w") as out:
        out.write("BB 2.5σ Breakout + WT/DC Scorer Backtest Results\n")
        out.write(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
        out.write(f"Capital: ${CAPITAL:,.0f}, Base size: ${BASE_SIZE:,.0f} (2%)\n")
        out.write(f"Entry threshold: {ENTRY_THRESHOLD}, Exit threshold: {EXIT_THRESHOLD}\n")
        out.write(f"Symbols: {', '.join(SYMBOLS)}\n")
        out.write(f"Period: 2024-01-01 to present, 13:30-20:00 UTC weekdays\n")
        out.write(f"Runtime: {total_elapsed:.1f}s\n")
        out.write("=" * 100 + "\n\n")

        # Summary
        out.write("SUMMARY\n")
        out.write("-" * 100 + "\n")
        out.write(f"{'Config':<16} {'Trades':>7} {'PnL':>14} {'WR%':>7} {'Sharpe':>8} "
                  f"{'MaxDD':>12} {'BB#':>6} {'BB_PnL':>12} {'PnL/Trade':>11}\n")
        out.write("-" * 100 + "\n")
        for config in configs:
            a = results[config]["agg"]
            ppt = a["total_pnl"] / a["total_trades"] if a["total_trades"] else 0
            out.write(f"{config:<16} {a['total_trades']:>7} ${a['total_pnl']:>+12,.2f} "
                      f"{a['total_wr']:>6.1f}% {a['agg_sharpe']:>+7.2f} "
                      f"${a['max_dd']:>10,.2f} {a['bb_count']:>6} "
                      f"${a['bb_pnl']:>+10,.0f} ${ppt:>+9,.2f}\n")
        out.write("-" * 100 + "\n\n")

        # Comparison vs baseline
        out.write("COMPARISON VS BASELINE\n")
        out.write("-" * 100 + "\n")
        base = results["BASELINE"]["agg"]
        for config in configs[1:]:
            a = results[config]["agg"]
            pnl_d = a["total_pnl"] - base["total_pnl"]
            sh_d = a["agg_sharpe"] - base["agg_sharpe"]
            out.write(f"  {config:<14}: PnL {'+'if pnl_d>=0 else ''}{pnl_d:>12,.2f} vs baseline, "
                      f"Sharpe {'+'if sh_d>=0 else ''}{sh_d:.2f}, "
                      f"Trades {a['total_trades']} vs {base['total_trades']}, "
                      f"MaxDD ${a['max_dd']:,.0f} vs ${base['max_dd']:,.0f}\n")
        out.write("\n")

        # Per-config per-symbol
        for config in configs:
            out.write(f"\n{'='*90}\n")
            out.write(f"CONFIG: {config}\n")
            out.write(f"{'='*90}\n")
            out.write(f"{'Symbol':<8} {'Trades':>7} {'PnL':>12} {'WR%':>7} {'Sharpe':>8} "
                      f"{'AvgHold':>8} {'MaxDD':>10} {'BB#':>5} {'BB_PnL':>10} {'W':>4} {'L':>4}\n")
            out.write("-" * 90 + "\n")
            for symbol in SYMBOLS:
                sr = results[config]["symbol_results"][symbol]
                out.write(f"{symbol:<8} {sr['trades']:>7} ${sr['pnl']:>+10,.2f} "
                          f"{sr['wr']:>6.1f}% {sr['sharpe']:>+7.2f} "
                          f"{sr['avg_hold_hrs']:>6.1f}h ${sr['max_dd']:>8,.0f} "
                          f"{sr['bb_boosted']:>5} ${sr['bb_boosted_pnl']:>+8,.0f} "
                          f"{sr['winners']:>4} {sr['losers']:>4}\n")
            out.write("-" * 90 + "\n")
            a = results[config]["agg"]
            out.write(f"{'TOTAL':<8} {a['total_trades']:>7} ${a['total_pnl']:>+10,.2f} "
                      f"{a['total_wr']:>6.1f}% {a['agg_sharpe']:>+7.2f} "
                      f"{'':>8} ${a['max_dd']:>8,.0f} {a['bb_count']:>5} "
                      f"${a['bb_pnl']:>+8,.0f}\n")

        # Per-symbol BB_BOOST vs BASELINE
        out.write(f"\n\n{'='*90}\n")
        out.write("PER-SYMBOL: PnL DIFFERENCE vs BASELINE\n")
        out.write(f"{'='*90}\n")
        out.write(f"{'Symbol':<8} {'BASELINE':>12} {'BB_BOOST':>12} {'Diff':>12} "
                  f"{'BB_BOOST_25':>12} {'Diff':>12} {'BB_ONLY':>12} {'BB_RETEST':>12}\n")
        out.write("-" * 90 + "\n")
        for symbol in SYMBOLS:
            bp = results["BASELINE"]["symbol_results"][symbol]["pnl"]
            b1 = results["BB_BOOST"]["symbol_results"][symbol]["pnl"]
            b2 = results["BB_BOOST_25"]["symbol_results"][symbol]["pnl"]
            bo = results["BB_ONLY"]["symbol_results"][symbol]["pnl"]
            br = results["BB_RETEST"]["symbol_results"][symbol]["pnl"]
            out.write(f"{symbol:<8} ${bp:>+10,.0f} ${b1:>+10,.0f} ${b1-bp:>+10,.0f} "
                      f"${b2:>+10,.0f} ${b2-bp:>+10,.0f} ${bo:>+10,.0f} ${br:>+10,.0f}\n")

        # Verdict
        out.write(f"\n\n{'='*90}\n")
        out.write("VERDICT\n")
        out.write(f"{'='*90}\n")
        best_sharpe_cfg = max(configs, key=lambda c: results[c]["agg"]["agg_sharpe"])
        best_pnl_cfg = max(configs, key=lambda c: results[c]["agg"]["total_pnl"])
        out.write(f"Best Sharpe: {best_sharpe_cfg} ({results[best_sharpe_cfg]['agg']['agg_sharpe']:+.2f})\n")
        out.write(f"Best PnL:    {best_pnl_cfg} (${results[best_pnl_cfg]['agg']['total_pnl']:+,.2f})\n")
        out.write(f"Baseline:    Sharpe={base['agg_sharpe']:+.2f}, PnL=${base['total_pnl']:+,.2f}\n")

        # BB boost edge analysis
        out.write(f"\nBB BOOST EDGE ANALYSIS:\n")
        for config in ["BB_BOOST", "BB_BOOST_25"]:
            at = results[config]["all_trades"]
            boosted = [t for t in at if t["size_mult"] > 1.0]
            unboosted = [t for t in at if t["size_mult"] <= 1.0]
            if boosted:
                b_avg = np.mean([t["pnl"] for t in boosted])
                b_wr = np.mean([1 if t["pnl"] > 0 else 0 for t in boosted]) * 100
            else:
                b_avg = 0
                b_wr = 0
            if unboosted:
                u_avg = np.mean([t["pnl"] for t in unboosted])
                u_wr = np.mean([1 if t["pnl"] > 0 else 0 for t in unboosted]) * 100
            else:
                u_avg = 0
                u_wr = 0
            out.write(f"  {config}: Boosted trades: {len(boosted)} (avg PnL=${b_avg:+,.2f}, WR={b_wr:.1f}%) "
                      f"vs Unboosted: {len(unboosted)} (avg PnL=${u_avg:+,.2f}, WR={u_wr:.1f}%)\n")

    print(f"\nResults written to: {output_path}")

    # Print verdict
    print("\n" + "=" * 60)
    print("VERDICT")
    print("=" * 60)
    best_sharpe_cfg = max(configs, key=lambda c: results[c]["agg"]["agg_sharpe"])
    best_pnl_cfg = max(configs, key=lambda c: results[c]["agg"]["total_pnl"])
    print(f"Best Sharpe: {best_sharpe_cfg} ({results[best_sharpe_cfg]['agg']['agg_sharpe']:+.2f} vs BASELINE {base['agg_sharpe']:+.2f})")
    print(f"Best PnL:    {best_pnl_cfg} (${results[best_pnl_cfg]['agg']['total_pnl']:+,.2f} vs BASELINE ${base['total_pnl']:+,.2f})")


if __name__ == "__main__":
    main()
