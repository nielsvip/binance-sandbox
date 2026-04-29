"""
Combined System Backtest: Delta Engine + WT/DC Scorers + BB Boost
=================================================================
Tests ALL live trading components together on NPZ data.

Modes:
A) Delta only (no scorer fallback)
B) Scorer only (no delta)
C) Combined (delta primary + scorer fallback)
D) Combined + BB boost (2x size when bb_pct_b_4h > 1.0)

Data: 5m bars from backtest_v8/indicators/ NPZ files
Trading hours: 13:30-20:00 UTC weekdays only
Capital: $180k, 2% per trade ($3,600 base)
Max hold: 96 bars (8 hours)
"""

import sys
import os
import numpy as np
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, "/Users/niels/Documents/binance")
from wt_dc_delta import DeltaTracker
from wt_dc_entry_scorer import score_entry
from wt_dc_exit_scorer import score_exit

NPZ_DIR = "/Users/niels/Documents/binance/backtest_v8/indicators"
SYMBOLS = ["MU", "AAPL", "TTD", "FIVN", "AMZN", "MRVL", "XOM", "CVX", "GLD", "USO", "NVDA", "MSFT", "ASTS"]

CAPITAL = 180_000.0
RISK_PER_TRADE = 0.02
BASE_SIZE = CAPITAL * RISK_PER_TRADE

ENTRY_SCORE_THRESHOLD = 37
EXIT_SCORE_THRESHOLD = 20
MAX_HOLD_BARS = 96

MARKET_OPEN_MIN = 13 * 60 + 30
MARKET_CLOSE_MIN = 20 * 60


class LazyBarDict:
    """Lazy dict-like object that reads from NPZ arrays on demand."""
    __slots__ = ('_data', '_i', '_cache')

    def __init__(self, data, i):
        self._data = data
        self._i = i
        self._cache = {}

    def get(self, key, default=None):
        if key in self._cache:
            return self._cache[key]
        arr = self._data.get(key)
        if arr is None:
            return default
        try:
            v = float(arr[self._i])
            if v != v:  # NaN
                return default
            self._cache[key] = v
            return v
        except (IndexError, ValueError, TypeError):
            return default

    def __getitem__(self, key):
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._data.keys()

    def items(self):
        for k in self._data:
            v = self.get(k)
            if v is not None:
                yield k, v


def load_symbol(symbol):
    """Load NPZ and return dict of arrays + precomputed trading hours mask."""
    path = os.path.join(NPZ_DIR, f"{symbol}.npz")
    f = np.load(path, allow_pickle=True)
    data = {}
    for k in f.files:
        arr = f[k]
        if arr.dtype in (np.float32, np.float64):
            data[k] = arr.astype(np.float64)
        else:
            data[k] = arr
    for k in list(data.keys()):
        if "bb_pct_b" in k and data[k].dtype in (np.float64,):
            arr = data[k].copy()
            arr[(arr > 10) | (arr < -10)] = np.nan
            data[k] = arr
    ts = data["timestamps"]
    n = len(ts)
    trading_mask = np.zeros(n, dtype=bool)
    for i in range(n):
        dt = datetime.fromtimestamp(ts[i], tz=timezone.utc)
        if dt.weekday() < 5:
            t = dt.hour * 60 + dt.minute
            if MARKET_OPEN_MIN <= t < MARKET_CLOSE_MIN:
                trading_mask[i] = True
    return data, trading_mask


def run_backtest_symbol(symbol, data, trading_mask, mode):
    """Run backtest for a single symbol. Returns list of trades."""
    use_delta = mode in ("delta_only", "combined", "combined_bb")
    use_scorer = mode in ("scorer_only", "combined", "combined_bb")
    use_bb_boost = mode == "combined_bb"

    ts = data["timestamps"]
    close = data["close"]
    n = len(close)

    # Get trading bar indices (skip first 200 for warmup)
    trading_indices = np.where(trading_mask)[0]
    trading_indices = trading_indices[trading_indices >= 200]

    tracker = DeltaTracker()
    in_position = False
    pos_side = None
    entry_price = 0.0
    entry_bar = 0
    entry_size = BASE_SIZE
    trades = []

    # For delta-only mode, we still need to update tracker on every trading bar
    # But we only call the expensive scorer when needed

    for i in trading_indices:
        price = close[i]
        if price <= 0 or np.isnan(price):
            continue

        ind = LazyBarDict(data, i)

        if not in_position:
            entered = False
            side = None
            size_mult = 1.0

            if use_bb_boost:
                bb_4h = ind.get("bb_pct_b_4h", 0.5)
                if bb_4h is not None and bb_4h > 1.0:
                    size_mult = 2.0

            if use_delta:
                sig = tracker.update(symbol, ind, None)
                if sig.entry_long:
                    side = "LONG"
                    entered = True
                elif sig.entry_short:
                    side = "SHORT"
                    entered = True

            if not entered and use_scorer:
                if not use_delta:
                    tracker.update(symbol, ind, None)
                long_score, _ = score_entry(ind, is_long=True)
                short_score, _ = score_entry(ind, is_long=False)
                if long_score >= ENTRY_SCORE_THRESHOLD and long_score > short_score:
                    side = "LONG"
                    entered = True
                elif short_score >= ENTRY_SCORE_THRESHOLD and short_score > long_score:
                    side = "SHORT"
                    entered = True

            if entered and side:
                in_position = True
                pos_side = side
                entry_price = price
                entry_bar = i
                entry_size = BASE_SIZE * size_mult
                tracker.reset_position_state(symbol)
                tracker._max_speed[symbol] = 0.0

        else:
            bars_held = i - entry_bar
            should_exit = False
            exit_reason = ""

            if use_delta:
                pos_state = {"side": pos_side, "n_entries": 1, "last_entry_price": entry_price}
                sig = tracker.update(symbol, ind, pos_state)
                if pos_side == "LONG" and sig.exit_long:
                    should_exit = True
                    exit_reason = "delta_exit"
                elif pos_side == "SHORT" and sig.exit_short:
                    should_exit = True
                    exit_reason = "delta_exit"

            if not should_exit and use_scorer:
                if not use_delta:
                    tracker.update(symbol, ind, None)
                exit_sc, _ = score_exit(ind, pos_side == "LONG")
                if exit_sc >= EXIT_SCORE_THRESHOLD:
                    should_exit = True
                    exit_reason = f"scorer_exit({exit_sc:.0f})"

            if not should_exit and bars_held >= MAX_HOLD_BARS:
                should_exit = True
                exit_reason = "max_hold"

            if should_exit:
                if pos_side == "LONG":
                    pnl_pct = (price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - price) / entry_price
                pnl_dollar = pnl_pct * entry_size
                trades.append({
                    "symbol": symbol,
                    "side": pos_side,
                    "entry_price": entry_price,
                    "exit_price": price,
                    "pnl_pct": pnl_pct,
                    "pnl_dollar": pnl_dollar,
                    "bars_held": bars_held,
                    "hold_minutes": bars_held * 5,
                    "exit_reason": exit_reason,
                    "size_mult": entry_size / BASE_SIZE,
                    "entry_ts": float(ts[entry_bar]),
                    "exit_ts": float(ts[i]),
                })
                in_position = False
                pos_side = None
                entry_price = 0.0
                entry_bar = 0
                entry_size = BASE_SIZE
                tracker.reset_position_state(symbol)

    return trades


def compute_stats(trades):
    """Compute stats from a list of trades."""
    if not trades:
        return {"trades": 0, "total_pnl": 0, "win_rate": 0, "profit_factor": 0,
                "avg_pnl_pct": 0, "avg_hold_min": 0, "weekly_sharpe": 0,
                "max_dd_trade": 0, "best_trade": 0, "long_trades": 0, "short_trades": 0,
                "delta_exits": 0, "scorer_exits": 0, "max_hold_exits": 0, "bb_boosted": 0}
    pnls = np.array([t["pnl_dollar"] for t in trades])
    wins = pnls > 0
    gp = np.sum(pnls[wins]) if np.any(wins) else 0
    gl = abs(np.sum(pnls[~wins])) if np.any(~wins) else 0.001
    # 2026-04-29 per CLAUDE.md rule 4: weekly $-Sharpe + sqrt(52) annualize → per-trade pool_sharpe.
    pnl_pcts = np.array([t.get("pnl_pct", t.get("pnl_dollar", 0.0)) for t in trades])
    ws = float(np.mean(pnl_pcts) / np.std(pnl_pcts)) if len(pnl_pcts) > 1 and np.std(pnl_pcts) > 0 else 0
    return {
        "trades": len(trades),
        "total_pnl": float(np.sum(pnls)),
        "win_rate": float(np.mean(wins) * 100),
        "profit_factor": float(gp / gl),
        "avg_pnl_pct": float(np.mean([t["pnl_pct"] for t in trades]) * 100),
        "avg_hold_min": float(np.mean([t["hold_minutes"] for t in trades])),
        "weekly_sharpe": float(ws),
        "max_dd_trade": float(np.min(pnls)),
        "best_trade": float(np.max(pnls)),
        "long_trades": sum(1 for t in trades if t["side"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["side"] == "SHORT"),
        "delta_exits": sum(1 for t in trades if t["exit_reason"] == "delta_exit"),
        "scorer_exits": sum(1 for t in trades if "scorer_exit" in t["exit_reason"]),
        "max_hold_exits": sum(1 for t in trades if t["exit_reason"] == "max_hold"),
        "bb_boosted": sum(1 for t in trades if t["size_mult"] > 1.0),
    }


def run_all_modes(symbols):
    """Run all 4 modes, sharing loaded data."""
    modes = ["delta_only", "scorer_only", "combined", "combined_bb"]
    mode_names = {
        "delta_only": "A) Delta Only",
        "scorer_only": "B) Scorer Only",
        "combined": "C) Combined (Delta + Scorer)",
        "combined_bb": "D) Combined + BB Boost",
    }
    # Load data once per symbol
    symbol_data = {}
    for sym in symbols:
        print(f"Loading {sym}...", flush=True)
        symbol_data[sym] = load_symbol(sym)
    results = {}
    for mode in modes:
        print(f"\nRunning {mode_names[mode]}...", flush=True)
        all_trades = []
        sym_stats = {}
        for sym in symbols:
            data, mask = symbol_data[sym]
            trades = run_backtest_symbol(sym, data, mask, mode)
            sym_stats[sym] = compute_stats(trades)
            all_trades.extend(trades)
            print(f"  {sym}: {len(trades)} trades, PnL=${sum(t['pnl_dollar'] for t in trades):,.2f}", flush=True)
        results[mode] = (all_trades, sym_stats)
    return results, mode_names


def format_results(mode_name, trades, stats, symbols):
    """Format results as text."""
    lines = []
    lines.append(f"\n{'='*80}")
    lines.append(f"MODE: {mode_name.upper()}")
    lines.append(f"{'='*80}")
    if not trades:
        lines.append("NO TRADES")
        return "\n".join(lines)
    s = compute_stats(trades)
    lines.append(f"\nOVERALL SUMMARY:")
    lines.append(f"  Total trades:      {s['trades']}")
    lines.append(f"  Total PnL:         ${s['total_pnl']:,.2f}")
    lines.append(f"  Win rate:          {s['win_rate']:.1f}%")
    lines.append(f"  Profit factor:     {s['profit_factor']:.2f}x")
    lines.append(f"  Avg PnL/trade:     ${s['total_pnl']/s['trades']:,.2f} ({s['avg_pnl_pct']:.3f}%)")
    lines.append(f"  Avg hold time:     {s['avg_hold_min']:.0f} min ({s['avg_hold_min']/60:.1f} hrs)")
    lines.append(f"  Weekly Sharpe:     {s['weekly_sharpe']:.2f}")
    lines.append(f"  Best trade:        ${s['best_trade']:,.2f}")
    lines.append(f"  Worst trade:       ${s['max_dd_trade']:,.2f}")
    lines.append(f"  Long/Short:        {s['long_trades']}/{s['short_trades']}")
    lines.append(f"  Exit breakdown:    delta={s['delta_exits']}, scorer={s['scorer_exits']}, max_hold={s['max_hold_exits']}")
    if s['bb_boosted'] > 0:
        bb_trades = [t for t in trades if t["size_mult"] > 1.0]
        bb_pnl = sum(t["pnl_dollar"] for t in bb_trades)
        lines.append(f"  BB boosted trades: {s['bb_boosted']} (PnL: ${bb_pnl:,.2f})")
    lines.append(f"\nPER-SYMBOL BREAKDOWN:")
    lines.append(f"  {'Symbol':<8} {'Trades':>6} {'PnL':>12} {'WR%':>6} {'PF':>6} {'AvgPnl%':>8} {'AvgHold':>8} {'wSharpe':>8} {'L/S':>7} {'D/S/M':>10}")
    lines.append(f"  {'-'*8} {'-'*6} {'-'*12} {'-'*6} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*10}")
    for sym in symbols:
        st = stats[sym]
        if st["trades"] == 0:
            lines.append(f"  {sym:<8} {'0':>6} {'$0':>12} {'---':>6} {'---':>6} {'---':>8} {'---':>8} {'---':>8} {'---':>7} {'---':>10}")
            continue
        lines.append(f"  {sym:<8} {st['trades']:>6} ${st['total_pnl']:>10,.2f} {st['win_rate']:>5.1f}% {st['profit_factor']:>5.2f}x {st['avg_pnl_pct']:>7.3f}% {st['avg_hold_min']:>6.0f}m {st['weekly_sharpe']:>7.2f} {st['long_trades']:>3}/{st['short_trades']:<3} {st['delta_exits']:>2}/{st['scorer_exits']:>2}/{st['max_hold_exits']:>2}")
    return "\n".join(lines)


def main():
    out = []
    out.append("COMBINED SYSTEM BACKTEST -- Delta + WT/DC Scorers + BB Boost")
    out.append("=" * 80)
    out.append(f"Run: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    out.append(f"Symbols: {', '.join(SYMBOLS)}")
    out.append(f"Data: 5m NPZ from backtest_v8/indicators/ (Apr 2024 - Mar 2026)")
    out.append(f"Capital: ${CAPITAL:,.0f}, Risk/trade: {RISK_PER_TRADE*100:.0f}% (${BASE_SIZE:,.0f})")
    out.append(f"Entry threshold (scorer): {ENTRY_SCORE_THRESHOLD}")
    out.append(f"Exit threshold (scorer): {EXIT_SCORE_THRESHOLD}")
    out.append(f"Max hold: {MAX_HOLD_BARS} bars ({MAX_HOLD_BARS*5/60:.0f} hrs)")
    out.append(f"Trading hours: 13:30-20:00 UTC (weekdays)")
    out.append(f"BB boost: 2x size when bb_pct_b_4h > 1.0")
    results, mode_names = run_all_modes(SYMBOLS)
    mode_order = ["delta_only", "scorer_only", "combined", "combined_bb"]
    for mk in mode_order:
        trades, stats = results[mk]
        out.append(format_results(mode_names[mk], trades, stats, SYMBOLS))
    # Comparison table
    out.append(f"\n{'='*80}")
    out.append("COMPARISON TABLE")
    out.append(f"{'='*80}")
    out.append(f"  {'Mode':<35} {'Trades':>7} {'PnL':>12} {'WR%':>6} {'PF':>6} {'wSharpe':>8}")
    out.append(f"  {'-'*35} {'-'*7} {'-'*12} {'-'*6} {'-'*6} {'-'*8}")
    for mk in mode_order:
        trades, _ = results[mk]
        s = compute_stats(trades)
        out.append(f"  {mode_names[mk]:<35} {s['trades']:>7} ${s['total_pnl']:>10,.2f} {s['win_rate']:>5.1f}% {s['profit_factor']:>5.2f}x {s['weekly_sharpe']:>7.2f}")
    # Verdict
    out.append(f"\n{'='*80}")
    out.append("VERDICT")
    out.append(f"{'='*80}")
    best_mk, best_ws = None, -999
    for mk in mode_order:
        s = compute_stats(results[mk][0])
        if s["weekly_sharpe"] > best_ws:
            best_ws = s["weekly_sharpe"]
            best_mk = mk
    out.append(f"Best mode by weekly Sharpe: {mode_names[best_mk]} (Sharpe={best_ws:.2f})")
    c_pnl = compute_stats(results["combined"][0])["total_pnl"]
    d_pnl = compute_stats(results["delta_only"][0])["total_pnl"]
    s_pnl = compute_stats(results["scorer_only"][0])["total_pnl"]
    if c_pnl > d_pnl and c_pnl > s_pnl:
        out.append("Systems COMPLEMENT each other (combined > either alone).")
    elif c_pnl < d_pnl and c_pnl < s_pnl:
        out.append("WARNING: Systems FIGHT each other (combined WORSE than either alone).")
    else:
        out.append(f"Mixed: Combined PnL ${c_pnl:,.2f} vs Delta ${d_pnl:,.2f} vs Scorer ${s_pnl:,.2f}.")
    cb_pnl = compute_stats(results["combined_bb"][0])["total_pnl"]
    bb_diff = cb_pnl - c_pnl
    out.append(f"BB boost effect: ${bb_diff:+,.2f} ({bb_diff/max(abs(c_pnl),1)*100:+.1f}%)")
    output = "\n".join(out)
    print(output)
    with open("/Users/niels/Documents/binance/combined_system_test.txt", "w") as f:
        f.write(output)
    print(f"\nResults written to /Users/niels/Documents/binance/combined_system_test.txt")


if __name__ == "__main__":
    main()
