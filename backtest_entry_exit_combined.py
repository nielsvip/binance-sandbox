#!/usr/bin/env python3
"""
Combined Entry + Exit Backtest using data-driven WT/DC scorers.
FAST version: pre-extracts only ~70 keys the scorers need, skips every other bar.

Usage:
    python backtest_entry_exit_combined.py
"""
import numpy as np
import sys
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from wt_dc_entry_scorer import score_entry
from wt_dc_exit_scorer import score_exit

NPZ_DIR = Path("/Users/niels/Documents/binance/backtest_v8/indicators")

# All keys actually used by score_entry and score_exit
ENTRY_KEYS = [
    "wt_bull_alignment", "wt_bear_alignment",
    "wt_velocity_up_count", "wt_velocity_down_count",
    "wt_structure_4h", "wt_structure_D",
    "wt_composite_bias",
    "wt_wave_phase_4h", "wt_wave_phase_D",
    "wt_cross_bull_1h", "wt_cross_bull_15m", "wt_cross_bull_5m",
    "wt_cross_bear_1h", "wt_cross_bear_15m", "wt_cross_bear_5m",
    "wt_velocity_1h", "wt_momentum_state_1h",
    "wt_cross_value_1h", "wt_cross_prev_value_1h",
    "wt_cross_rising_1h",
    "wt_divergence_1h", "wt_divergence_strength_1h",
    "dc_position_1h", "dc_position_4h",
    "bb_pct_b_4h", "bb_pct_b_D",
    "relative_volume_1h", "mfi_1h",
    "stoch_crossover_15m", "stoch_crossunder_15m",
    "stoch_k_1h",
    "close_D", "ema_200_D",
    "close_1h", "ema_20_1h",
    "ha_4h", "ha_D",
    "close_5m", "close_15m", "close",
]

EXIT_KEYS = [
    "wt_velocity_5m", "wt_velocity_15m", "wt_velocity_1h", "wt_velocity_4h", "wt_velocity_D",
    "wt_acceleration_5m", "wt_acceleration_15m", "wt_acceleration_1h", "wt_acceleration_4h", "wt_acceleration_D",
    "wt_peak_15m", "wt_peak_1h", "wt_peak_4h", "wt_peak_D",
    "wt_peak_prev_15m", "wt_peak_prev_1h", "wt_peak_prev_4h", "wt_peak_prev_D",
    "wt_trough_15m", "wt_trough_1h", "wt_trough_4h", "wt_trough_D",
    "wt_trough_prev_15m", "wt_trough_prev_1h", "wt_trough_prev_4h", "wt_trough_prev_D",
    "wt_composite_long", "wt_composite_short",
    "wt_cross_bear_5m", "wt_cross_bear_15m", "wt_cross_bear_1h", "wt_cross_bear_4h", "wt_cross_bear_D",
    "wt_cross_bull_5m", "wt_cross_bull_15m", "wt_cross_bull_1h", "wt_cross_bull_4h", "wt_cross_bull_D",
    "wt_overbought_tf_count", "wt_oversold_tf_count",
    "stoch_k_1h", "stoch_k_1h_prev", "stoch_k_4h", "stoch_k_4h_prev",
]

# Combine and deduplicate
ALL_NEEDED_KEYS = sorted(set(ENTRY_KEYS + EXIT_KEYS))


def load_slim_npz(symbol: str):
    """Load only the keys we need from NPZ. Returns (arrays_dict, timestamps) or (None, None)."""
    p = NPZ_DIR / f"{symbol}.npz"
    if not p.exists():
        return None, None
    try:
        data = np.load(p, allow_pickle=True)
        available = set(data.files)
        ts = data["timestamps"] if "timestamps" in available else None
        if ts is None or len(ts) < 500:
            return None, None
        n = len(ts)
        arrays = {}
        for k in ALL_NEEDED_KEYS:
            if k in available:
                arr = data[k]
                if len(arr) != n:
                    continue  # Skip arrays that don't match timestamp length (5m vs 15m)
                if arr.dtype == object:
                    arrays[k] = arr
                else:
                    arrays[k] = arr.astype(np.float32)
        # Price keys for current_price
        for pk in ["close_5m", "close_15m", "close"]:
            if pk in available and pk not in arrays:
                arr = data[pk]
                if len(arr) == n:
                    arrays[pk] = arr.astype(np.float32)
        return arrays, ts.astype(np.float64)
    except Exception as e:
        return None, None


def build_slim_dict(arrays: dict, idx: int) -> dict:
    """Build indicator dict from pre-extracted slim arrays. All arrays are float32."""
    d = {}
    for key, arr in arrays.items():
        v = float(arr[idx])
        d[key] = 0.0 if v != v else v  # NaN check via self-inequality
    d["current_price"] = d.get("close_5m", d.get("close_15m", d.get("close", 0.0)))
    return d


def run_backtest(symbols: list, capital: float, entry_threshold: float,
                 exit_threshold: float, start_date: str, is_tradier: bool,
                 leverage: float = 1.0, progress: bool = True):
    """Run entry/exit backtest. Skip every other bar for speed."""
    start_ts = datetime.strptime(start_date, "%Y-%m-%d").timestamp()
    position_size = capital * 0.02 * leverage

    all_trades = []
    symbols_tested = 0
    symbols_skipped = 0

    for si, sym in enumerate(symbols):
        arrays, timestamps = load_slim_npz(sym)
        if arrays is None:
            symbols_skipped += 1
            continue

        n_bars = len(timestamps)
        symbols_tested += 1

        # Pre-compute trading hours mask for tradier (skip weekends + outside 13:30-20:00 UTC)
        if is_tradier:
            # Pure numpy: seconds since midnight UTC and day-of-week
            ts_int = timestamps.astype(np.int64)
            secs_of_day = ts_int % 86400
            hours_f = secs_of_day / 3600.0
            # day of week: epoch (1970-01-01) was Thursday=3, so (days+3)%7 gives 0=Mon
            days_since_epoch = ts_int // 86400
            dow = (days_since_epoch + 3) % 7  # 0=Mon, 5=Sat, 6=Sun
            tradeable = (hours_f >= 13.5) & (hours_f <= 20.0) & (dow < 5)
        else:
            tradeable = np.ones(n_bars, dtype=bool)

        # Pre-compute start mask
        valid = (timestamps >= start_ts) & tradeable
        valid[:200] = False  # warmup

        # Find valid indices, then sample every 2nd bar (10 min effective resolution)
        valid_indices = np.where(valid)[0]
        if len(valid_indices) == 0:
            continue
        sampled_indices = valid_indices[::2]

        # Get price array
        price_key = "close_5m" if "close_5m" in arrays else ("close_15m" if "close_15m" in arrays else "close")
        if price_key not in arrays:
            continue
        prices = arrays[price_key]

        # Pre-compute cross signal masks for fast entry pre-filter
        # Entry scores >45 almost always require at least one cross on LTF
        bull_cross = np.zeros(n_bars, dtype=bool)
        bear_cross = np.zeros(n_bars, dtype=bool)
        for ck in ["wt_cross_bull_1h", "wt_cross_bull_15m", "wt_cross_bull_5m"]:
            if ck in arrays:
                bull_cross |= (arrays[ck] != 0)
        for ck in ["wt_cross_bear_1h", "wt_cross_bear_15m", "wt_cross_bear_5m"]:
            if ck in arrays:
                bear_cross |= (arrays[ck] != 0)

        for is_long in [True, False]:
            position = None
            cross_mask = bull_cross if is_long else bear_cross

            for idx in sampled_indices:
                price = float(prices[idx])
                if price <= 0 or np.isnan(price):
                    continue

                if position is None:
                    # Fast pre-filter: skip bars without any cross signal
                    if not cross_mask[idx]:
                        continue

                    ind = build_slim_dict(arrays, idx)
                    entry_score, entry_reason = score_entry(ind, is_long)
                    if entry_score >= entry_threshold:
                        position = {
                            "entry_price": price,
                            "entry_idx": idx,
                            "entry_score": entry_score,
                            "entry_reason": entry_reason,
                            "entry_ts": float(timestamps[idx]),
                            "max_gain": 0.0,
                        }
                else:
                    entry_price = position["entry_price"]
                    if is_long:
                        gain_pct = (price - entry_price) / entry_price * 100
                    else:
                        gain_pct = (entry_price - price) / entry_price * 100
                    position["max_gain"] = max(position["max_gain"], gain_pct)
                    hold_bars = idx - position["entry_idx"]
                    hold_min = hold_bars * 5

                    if hold_min < 10:
                        continue

                    ind = build_slim_dict(arrays, idx)
                    exit_score, exit_reason = score_exit(ind, is_long)

                    should_exit = exit_score >= exit_threshold
                    if hold_bars >= 96:
                        should_exit = True
                        exit_reason = f"MAX_HOLD_{hold_bars}bars"
                        exit_score = 99

                    if should_exit:
                        all_trades.append({
                            "symbol": sym,
                            "side": "LONG" if is_long else "SHORT",
                            "entry_price": entry_price,
                            "exit_price": price,
                            "pnl_pct": gain_pct,
                            "pnl_usd": gain_pct / 100 * position_size,
                            "hold_bars": hold_bars,
                            "hold_min": hold_min,
                            "entry_score": position["entry_score"],
                            "exit_score": exit_score,
                            "max_gain": position["max_gain"],
                            "entry_reason": position["entry_reason"],
                            "exit_reason": exit_reason,
                        })
                        position = None

            # Force close open position at end
            if position is not None:
                idx = n_bars - 1
                price = float(prices[idx])
                if price > 0 and not np.isnan(price):
                    entry_price = position["entry_price"]
                    gain_pct = ((price - entry_price) / entry_price * 100) if is_long else ((entry_price - price) / entry_price * 100)
                    all_trades.append({
                        "symbol": sym, "side": "LONG" if is_long else "SHORT",
                        "entry_price": entry_price, "exit_price": price,
                        "pnl_pct": gain_pct, "pnl_usd": gain_pct / 100 * position_size,
                        "hold_bars": idx - position["entry_idx"],
                        "hold_min": (idx - position["entry_idx"]) * 5,
                        "entry_score": position["entry_score"], "exit_score": 0,
                        "max_gain": position["max_gain"],
                        "entry_reason": position["entry_reason"],
                        "exit_reason": "END_OF_DATA",
                    })

        if progress and symbols_tested % 20 == 0:
            print(f"  [{symbols_tested}/{len(symbols)}] {len(all_trades)} trades so far...")

    return all_trades, symbols_tested


def compute_metrics(trades: list, capital: float):
    """Compute all metrics from trade list. Returns dict."""
    if not trades:
        return None
    pnls = np.array([t["pnl_pct"] for t in trades])
    pnl_usd = np.array([t["pnl_usd"] for t in trades])
    n = len(trades)
    winners = pnls[pnls > 0]
    losers = pnls[pnls <= 0]
    wr = len(winners) / n * 100
    win_sum = np.sum(winners) if len(winners) > 0 else 0
    loss_sum = np.sum(losers) if len(losers) > 0 else 0
    pf = abs(win_sum / loss_sum) if loss_sum != 0 else 999.0
    sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252 * 12)) if np.std(pnls) > 0 else 0.0
    total_pnl = float(np.sum(pnl_usd))
    longs = [t for t in trades if t["side"] == "LONG"]
    shorts = [t for t in trades if t["side"] == "SHORT"]
    l_wr = len([t for t in longs if t["pnl_pct"] > 0]) / len(longs) * 100 if longs else 0
    s_wr = len([t for t in shorts if t["pnl_pct"] > 0]) / len(shorts) * 100 if shorts else 0
    return {
        "n": n, "wr": wr, "l_wr": l_wr, "s_wr": s_wr,
        "pf": pf, "sharpe": sharpe, "total_pnl": total_pnl,
        "avg_hold": float(np.mean([t["hold_min"] for t in trades])),
        "max_dd_trade": float(np.min(pnls)),
        "n_long": len(longs), "n_short": len(shorts),
    }


def one_line(mode: str, m: dict):
    """Print one-line summary in requested format."""
    print(f"{mode}: {m['n']} trades | Sharpe {m['sharpe']:.2f} | WR {m['wr']:.1f}% (L:{m['l_wr']:.1f}% S:{m['s_wr']:.1f}%) | PF {m['pf']:.2f}x | TotalPnL ${m['total_pnl']:,.0f}")


def print_full(mode: str, m: dict, capital: float, entry_t: float, exit_t: float, n_sym: int):
    """Print detailed results block."""
    print(f"\n{'='*70}")
    print(f"  COMBINED ENTRY+EXIT BACKTEST -- {mode}")
    print(f"  Entry>={entry_t} | Exit>={exit_t} | Capital: ${capital:,.0f} | Symbols: {n_sym}")
    print(f"{'='*70}")
    print(f"  Trades:     {m['n']:,} ({m['n_long']:,} L / {m['n_short']:,} S)")
    print(f"  Win Rate:   {m['wr']:.1f}% (L:{m['l_wr']:.1f}% S:{m['s_wr']:.1f}%)")
    print(f"  Profit Factor: {m['pf']:.2f}x")
    print(f"  Sharpe:     {m['sharpe']:.2f}")
    print(f"  Total PnL:  ${m['total_pnl']:,.2f}")
    print(f"  Avg Hold:   {m['avg_hold']:.0f} min ({m['avg_hold']/60:.1f}h)")
    print(f"  Max DD trade: {m['max_dd_trade']:.2f}%")
    print(f"{'='*70}")


def main():
    t0_total = time.time()

    all_npz = sorted([f.stem for f in NPZ_DIR.glob("*.npz")])
    tradier_symbols = [s for s in all_npz if not s.endswith("USDT") and s not in ("SPY", "QQQ", "BTC", "BTCL", "SBIT", "COMPUSDT")]
    crypto_symbols = [s for s in all_npz if s.endswith("USDT") and s != "COMPUSDT"]

    start_date = "2024-01-01"
    tradier_capital = 180000
    crypto_capital = 200
    crypto_leverage = 20.0

    # =============================================
    # Phase 1: Best single config (entry=55, exit=25)
    # =============================================
    print("\n" + "=" * 70)
    print("  PHASE 1: Single best config (entry>=37, exit>=20)")
    print("=" * 70)

    # TRADIER
    print(f"\n--- TRADIER ({len(tradier_symbols)} symbols, ${tradier_capital:,}) ---")
    t0 = time.time()
    trades_t, n_t = run_backtest(tradier_symbols, tradier_capital, 37, 20, start_date, is_tradier=True)
    elapsed_t = time.time() - t0
    m_t = compute_metrics(trades_t, tradier_capital)
    if m_t:
        one_line("TRADIER", m_t)
        print_full("TRADIER", m_t, tradier_capital, 55, 25, n_t)
    else:
        print("TRADIER: NO TRADES")
    print(f"  Time: {elapsed_t:.1f}s")

    # CRYPTO
    print(f"\n--- CRYPTO ({len(crypto_symbols)} symbols, ${crypto_capital} x{crypto_leverage:.0f}x) ---")
    t0 = time.time()
    trades_c, n_c = run_backtest(crypto_symbols, crypto_capital, 37, 20, start_date, is_tradier=False, leverage=crypto_leverage)
    elapsed_c = time.time() - t0
    m_c = compute_metrics(trades_c, crypto_capital)
    if m_c:
        one_line("CRYPTO", m_c)
        print_full("CRYPTO", m_c, crypto_capital, 55, 25, n_c)
    else:
        print("CRYPTO: NO TRADES")
    print(f"  Time: {elapsed_c:.1f}s")

    # =============================================
    # Phase 2: Threshold sweep (3x3 grid)
    # =============================================
    print("\n" + "=" * 70)
    print("  PHASE 2: Threshold sweep [45,55,65] x [20,25,35]")
    print("=" * 70)

    entry_thresholds = [35, 37, 40, 45]
    exit_thresholds = [20, 25, 35]

    # Print header
    print(f"\n{'':>10}", end="")
    for xt in exit_thresholds:
        print(f"  exit>={xt:>2}          ", end="")
    print()

    for mode, symbols, capital, is_tradier, lev in [
        ("TRADIER", tradier_symbols, tradier_capital, True, 1.0),
        ("CRYPTO", crypto_symbols, crypto_capital, False, crypto_leverage),
    ]:
        print(f"\n  --- {mode} ---")
        print(f"{'entry':>10}", end="")
        for xt in exit_thresholds:
            print(f"  {'Sharpe':>6} {'WR%':>5} {'PF':>5} {'#T':>5}", end="")
        print()
        print("-" * (10 + len(exit_thresholds) * 24))

        best_sharpe = -999
        best_config = None

        for et in entry_thresholds:
            print(f"  >={et:>4}  ", end="")
            for xt in exit_thresholds:
                trades, n_sym = run_backtest(symbols, capital, et, xt, start_date, is_tradier=is_tradier, leverage=lev, progress=False)
                m = compute_metrics(trades, capital)
                if m:
                    print(f"  {m['sharpe']:>6.2f} {m['wr']:>5.1f} {m['pf']:>5.2f} {m['n']:>5}", end="")
                    if m['sharpe'] > best_sharpe:
                        best_sharpe = m['sharpe']
                        best_config = (et, xt, m)
                else:
                    print(f"  {'---':>6} {'---':>5} {'---':>5} {'0':>5}", end="")
            print()

        if best_config:
            et, xt, m = best_config
            print(f"\n  BEST {mode}: entry>={et}, exit>={xt}")
            one_line(f"  {mode}", m)

    total_elapsed = time.time() - t0_total
    print(f"\n{'='*70}")
    print(f"  TOTAL TIME: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
