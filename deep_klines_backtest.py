"""
deep_klines_backtest.py
=======================
Comprehensive RSI + Stochastic entry filter backtest on raw klines data.
Tests 10 filter conditions × 4 timeframes × 20 crypto symbols.
Also runs cross-timeframe approval tests (15m→1h, 1h→4h confirmation).

Usage:
    /opt/anaconda3/envs/binance_env/bin/python deep_klines_backtest.py
"""

import os
import json
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE_DIR = "/Users/niels/Documents/binance/klines_cache"
TIMEFRAMES = ["15m", "1h", "4h", "D"]
MIN_BARS = 1500  # target; we use whatever is available (>=200 minimum)
MIN_BARS_FLOOR = 200

# 20 crypto symbols — chosen for data depth
CRYPTO_SYMBOLS = [
    "NKNUSDT",
    "SXPUSDT",
    "ICXUSDT",
    "1000XECUSDT",
    "1INCHUSDT",
    "INJUSDT",
    "BTCUSDC",
    "ETHUSDC",
    "AAVEUSDC",
    "1000SHIBUSDC",
    "ARBUSDC",
    "ANKRUSDT",
    "API3USDT",
    "APEUSDT",
    "APTUSDT",
    "FXSUSDT",
    "1000FLOKIUSDT",
    "OMUSDT",
    "YGGUSDT",
    "ZRXUSDT",
]

# --- Indicator helpers (pure numpy/pandas, no ta-lib) ---


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    denom = highest_high - lowest_low
    raw_k = 100 * (close - lowest_low) / denom.replace(0, np.nan)
    k = raw_k.rolling(d_period).mean()  # smoothed %K
    d = k.rolling(d_period).mean()  # %D signal line
    return k, d


# --- Data loading ---


def load_klines(symbol: str, timeframe: str) -> pd.DataFrame | None:
    path = os.path.join(CACHE_DIR, f"{symbol}_{timeframe}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        raw = json.load(f)
    if not raw:
        return None
    df = pd.DataFrame(raw)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    return df


def build_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Attach RSI, stoch K/D, and crossover signals to dataframe."""
    df = df.copy()
    df["rsi"] = rsi(df["close"])
    df["stoch_k"], df["stoch_d"] = stochastic(df["high"], df["low"], df["close"])
    # K crosses above D = bullish crossover (long signal)
    df["k_above_d"] = df["stoch_k"] > df["stoch_d"]
    df["long_signal"] = (~df["k_above_d"].shift(1).fillna(True)) & df["k_above_d"]
    # K crosses below D = bearish crossunder (short signal)
    df["short_signal"] = df["k_above_d"].shift(1).fillna(False) & (~df["k_above_d"])
    return df


# --- Trade simulation ---


def simulate_trades(
    df: pd.DataFrame, direction: str = "LONG", entry_mask: pd.Series | None = None
) -> list[dict]:
    """
    Simulate entries on stoch crossover/crossunder, exit on opposite crossover.
    direction: 'LONG' enters on bullish cross, exits on bearish cross.
               'SHORT' enters on bearish cross, exits on bullish cross.
    entry_mask: additional boolean mask applied at entry bar.
    Returns list of trade dicts with gain_pct.
    """
    trades = []
    in_trade = False
    entry_price = None
    entry_idx = None

    if entry_mask is None:
        entry_mask = pd.Series(True, index=df.index)

    if direction == "LONG":
        enter_col = "long_signal"
        exit_col = "short_signal"
    else:
        enter_col = "short_signal"
        exit_col = "long_signal"

    for i in range(len(df)):
        if not in_trade:
            if df[enter_col].iloc[i] and entry_mask.iloc[i]:
                in_trade = True
                entry_price = df["close"].iloc[i]
                entry_idx = i
        else:
            if df[exit_col].iloc[i]:
                exit_price = df["close"].iloc[i]
                if direction == "LONG":
                    gain_pct = (exit_price - entry_price) / entry_price * 100
                else:
                    gain_pct = (entry_price - exit_price) / entry_price * 100
                trades.append(
                    {
                        "entry_idx": entry_idx,
                        "exit_idx": i,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "gain_pct": gain_pct,
                        "bars_held": i - entry_idx,
                    }
                )
                in_trade = False

    return trades


def trade_stats(trades: list[dict]) -> dict:
    if not trades:
        return {
            "count": 0,
            "avg_gain": 0.0,
            "win_rate": 0.0,
            "median_gain": 0.0,
            "max_drawdown": 0.0,
            "total_gain": 0.0,
        }
    gains = [t["gain_pct"] for t in trades]
    wins = sum(1 for g in gains if g > 0)
    # Max drawdown: peak-to-trough on cumulative equity
    cum = np.cumsum(gains)
    peak = np.maximum.accumulate(cum)
    drawdowns = peak - cum
    return {
        "count": len(trades),
        "avg_gain": float(np.mean(gains)),
        "win_rate": float(wins / len(trades) * 100),
        "median_gain": float(np.median(gains)),
        "max_drawdown": float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0,
        "total_gain": float(np.sum(gains)),
    }


# --- Filter definitions ---

FILTERS = {
    # (filter_name, direction, lambda df -> entry_mask)
    "1_baseline": lambda df, d: pd.Series(True, index=df.index),
    "2_rsi_gt50": lambda df, d: df["rsi"] > 50 if d == "LONG" else df["rsi"] < 50,
    "3_rsi_lt50": lambda df, d: df["rsi"] < 50 if d == "LONG" else df["rsi"] > 50,
    "4_rsi_gt30": lambda df, d: df["rsi"] > 30 if d == "LONG" else df["rsi"] < 70,
    "5_rsi_lt70": lambda df, d: df["rsi"] < 70 if d == "LONG" else df["rsi"] > 30,
    "6_rsi_30_70": lambda df, d: (df["rsi"] >= 30) & (df["rsi"] <= 70),
    "7_rsi_lt20_deep": lambda df, d: df["rsi"] < 20 if d == "LONG" else df["rsi"] > 80,
    "8_rsi_gt80_deep": lambda df, d: df["rsi"] > 80 if d == "LONG" else df["rsi"] < 20,
    "9_stoch_k_lt30": lambda df, d: df["stoch_k"] < 30 if d == "LONG" else df["stoch_k"] > 70,
    "10_stoch_k_gt70": lambda df, d: df["stoch_k"] > 70 if d == "LONG" else df["stoch_k"] < 30,
}


# --- Main single-timeframe backtest ---


def run_single_tf(symbols: list[str], timeframe: str) -> pd.DataFrame:
    rows = []
    for sym in symbols:
        df = load_klines(sym, timeframe)
        if df is None or len(df) < MIN_BARS_FLOOR:
            continue
        df = build_signals(df)
        # Drop warmup (need at least 50 bars of indicator warmup)
        df = df.iloc[50:].reset_index(drop=True)
        if len(df) < 50:
            continue

        for direction in ["LONG", "SHORT"]:
            for filter_name, filter_fn in FILTERS.items():
                mask = filter_fn(df, direction).fillna(False)
                trades = simulate_trades(df, direction, mask)
                stats = trade_stats(trades)
                stats["symbol"] = sym
                stats["timeframe"] = timeframe
                stats["direction"] = direction
                stats["filter"] = filter_name
                stats["bars_available"] = len(df)
                rows.append(stats)

    return pd.DataFrame(rows)


# --- Cross-timeframe approval tests ---


def run_cross_tf(symbols: list[str]) -> pd.DataFrame:
    """
    Test three scenarios:
    A) Enter on 15m stoch crossover alone
    B) Enter on 15m stoch crossover ONLY when 1h RSI agrees (>50 long / <50 short)
    C) Enter on 1h stoch crossover ONLY when 4h RSI agrees (>50 long / <50 short)
    """
    rows = []

    for sym in symbols:
        df_15m = load_klines(sym, "15m")
        df_1h = load_klines(sym, "1h")
        df_4h = load_klines(sym, "4h")

        for direction in ["LONG", "SHORT"]:
            # --- Scenario A: 15m alone ---
            if df_15m is not None and len(df_15m) >= MIN_BARS_FLOOR:
                df15 = build_signals(df_15m).iloc[50:].reset_index(drop=True)
                trades_a = simulate_trades(df15, direction)
                stats_a = trade_stats(trades_a)
                stats_a.update(
                    {"symbol": sym, "scenario": "A_15m_alone", "direction": direction}
                )
                rows.append(stats_a)

            # --- Scenario B: 15m crossover + 1h RSI agreement ---
            if (
                df_15m is not None
                and df_1h is not None
                and len(df_15m) >= MIN_BARS_FLOOR
                and len(df_1h) >= MIN_BARS_FLOOR
            ):
                df15 = build_signals(df_15m).iloc[50:].reset_index(drop=True)
                df1h = build_signals(df_1h).iloc[50:].reset_index(drop=True)

                # Resample 1h RSI to 15m timestamps via forward-fill
                df1h_rsi = df1h[["timestamp", "rsi"]].set_index("timestamp").rename(columns={"rsi": "rsi_1h"})
                df15 = df15.set_index("timestamp")
                df15 = df15.join(df1h_rsi.reindex(df15.index, method="ffill"))
                df15 = df15.reset_index()

                if direction == "LONG":
                    mask_b = df15["rsi_1h"] > 50
                else:
                    mask_b = df15["rsi_1h"] < 50
                mask_b = mask_b.fillna(False)

                trades_b = simulate_trades(df15, direction, mask_b)
                stats_b = trade_stats(trades_b)
                stats_b.update(
                    {"symbol": sym, "scenario": "B_15m+1h_rsi", "direction": direction}
                )
                rows.append(stats_b)

            # --- Scenario C: 1h crossover + 4h RSI agreement ---
            if (
                df_1h is not None
                and df_4h is not None
                and len(df_1h) >= MIN_BARS_FLOOR
                and len(df_4h) >= MIN_BARS_FLOOR
            ):
                df1h = build_signals(df_1h).iloc[50:].reset_index(drop=True)
                df4h = build_signals(df_4h).iloc[50:].reset_index(drop=True)

                df4h_rsi = df4h[["timestamp", "rsi"]].set_index("timestamp").rename(columns={"rsi": "rsi_4h"})
                df1h = df1h.set_index("timestamp")
                df1h = df1h.join(df4h_rsi.reindex(df1h.index, method="ffill"))
                df1h = df1h.reset_index()

                if direction == "LONG":
                    mask_c = df1h["rsi_4h"] > 50
                else:
                    mask_c = df1h["rsi_4h"] < 50
                mask_c = mask_c.fillna(False)

                trades_c = simulate_trades(df1h, direction, mask_c)
                stats_c = trade_stats(trades_c)
                stats_c.update(
                    {"symbol": sym, "scenario": "C_1h+4h_rsi", "direction": direction}
                )
                rows.append(stats_c)

            # 1h alone for comparison with C
            if df_1h is not None and len(df_1h) >= MIN_BARS_FLOOR:
                df1h_plain = build_signals(df_1h).iloc[50:].reset_index(drop=True)
                trades_1h = simulate_trades(df1h_plain, direction)
                stats_1h = trade_stats(trades_1h)
                stats_1h.update(
                    {"symbol": sym, "scenario": "D_1h_alone", "direction": direction}
                )
                rows.append(stats_1h)

    return pd.DataFrame(rows)


# --- Aggregation helpers ---


def agg_by_filter(df: pd.DataFrame, timeframe: str, direction: str) -> pd.DataFrame:
    sub = df[(df["timeframe"] == timeframe) & (df["direction"] == direction)]
    if sub.empty:
        return pd.DataFrame()
    result = (
        sub.groupby("filter")
        .agg(
            symbols=("symbol", "count"),
            total_trades=("count", "sum"),
            avg_gain=("avg_gain", "mean"),
            win_rate=("win_rate", "mean"),
            median_gain=("median_gain", "mean"),
            max_drawdown=("max_drawdown", "mean"),
            total_gain=("total_gain", "sum"),
        )
        .reset_index()
    )
    # Compute vs baseline
    baseline_row = result[result["filter"] == "1_baseline"]
    if not baseline_row.empty:
        base_avg = baseline_row["avg_gain"].values[0]
        base_wr = baseline_row["win_rate"].values[0]
        result["vs_base_avg_gain"] = result["avg_gain"] - base_avg
        result["vs_base_win_rate"] = result["win_rate"] - base_wr
    result = result.sort_values("filter")
    return result


def print_table(df: pd.DataFrame, title: str):
    print(f"\n{'='*90}")
    print(f"  {title}")
    print(f"{'='*90}")
    if df.empty:
        print("  [no data]")
        return
    cols = [
        "filter",
        "symbols",
        "total_trades",
        "avg_gain",
        "win_rate",
        "median_gain",
        "max_drawdown",
        "vs_base_avg_gain",
        "vs_base_win_rate",
    ]
    cols = [c for c in cols if c in df.columns]
    header = (
        f"{'Filter':<25} {'Syms':>5} {'Trades':>7} {'AvgGain%':>9} "
        f"{'WinRate%':>9} {'MedGain%':>9} {'MaxDD%':>8} {'vsBsAvg':>8} {'vsBsWR':>7}"
    )
    print(header)
    print("-" * 90)
    for _, row in df.iterrows():
        vba = row.get("vs_base_avg_gain", 0)
        vbw = row.get("vs_base_win_rate", 0)
        vba_str = f"{vba:+.2f}" if not pd.isna(vba) else "  —  "
        vbw_str = f"{vbw:+.2f}" if not pd.isna(vbw) else "  —  "
        print(
            f"{row['filter']:<25} {int(row['symbols']):>5} {int(row['total_trades']):>7} "
            f"{row['avg_gain']:>+9.3f} {row['win_rate']:>9.1f} {row['median_gain']:>+9.3f} "
            f"{row['max_drawdown']:>8.2f} {vba_str:>8} {vbw_str:>7}"
        )


def print_cross_tf_table(df: pd.DataFrame, direction: str):
    print(f"\n{'='*90}")
    print(f"  Cross-Timeframe Approval — {direction}")
    print(f"{'='*90}")
    sub = df[df["direction"] == direction]
    if sub.empty:
        print("  [no data]")
        return
    result = (
        sub.groupby("scenario")
        .agg(
            symbols=("symbol", "count"),
            total_trades=("count", "sum"),
            avg_gain=("avg_gain", "mean"),
            win_rate=("win_rate", "mean"),
            median_gain=("median_gain", "mean"),
            max_drawdown=("max_drawdown", "mean"),
        )
        .reset_index()
        .sort_values("scenario")
    )
    header = (
        f"{'Scenario':<22} {'Syms':>5} {'Trades':>7} {'AvgGain%':>9} "
        f"{'WinRate%':>9} {'MedGain%':>9} {'MaxDD%':>8}"
    )
    print(header)
    print("-" * 75)
    for _, row in result.iterrows():
        print(
            f"{row['scenario']:<22} {int(row['symbols']):>5} {int(row['total_trades']):>7} "
            f"{row['avg_gain']:>+9.3f} {row['win_rate']:>9.1f} {row['median_gain']:>+9.3f} "
            f"{row['max_drawdown']:>8.2f}"
        )


def print_consistency_check(single_tf_df: pd.DataFrame):
    """Show which filters consistently improve avg_gain across ALL timeframes."""
    print(f"\n{'='*90}")
    print("  Consistency Check: filters that improve avg_gain vs baseline ACROSS ALL 4 timeframes")
    print(f"{'='*90}")
    for direction in ["LONG", "SHORT"]:
        print(f"\n  Direction: {direction}")
        filter_improvements = {}
        for tf in TIMEFRAMES:
            sub = single_tf_df[
                (single_tf_df["timeframe"] == tf) & (single_tf_df["direction"] == direction)
            ]
            if sub.empty:
                continue
            agg = sub.groupby("filter")["avg_gain"].mean()
            baseline_val = agg.get("1_baseline", None)
            if baseline_val is None:
                continue
            for f, val in agg.items():
                if f not in filter_improvements:
                    filter_improvements[f] = []
                filter_improvements[f].append((tf, val - baseline_val))

        print(f"  {'Filter':<25} {'15m':>8} {'1h':>8} {'4h':>8} {'D':>8} {'Consistent':>11}")
        print("  " + "-" * 72)
        for f in sorted(filter_improvements.keys()):
            tfs_data = dict(filter_improvements[f])
            vals = [tfs_data.get(tf, None) for tf in TIMEFRAMES]
            consistent = all(v is not None and v > 0 for v in vals)
            flag = "YES ***" if consistent else "no"
            val_strs = [f"{v:+.3f}" if v is not None else "  N/A " for v in vals]
            print(f"  {f:<25} {val_strs[0]:>8} {val_strs[1]:>8} {val_strs[2]:>8} {val_strs[3]:>8} {flag:>11}")


# --- Main ---


def main():
    print("=" * 90)
    print("  DEEP KLINES BACKTEST — RSI + Stochastic Entry Filters")
    print(f"  Symbols: {len(CRYPTO_SYMBOLS)} | Timeframes: {TIMEFRAMES} | Min bars floor: {MIN_BARS_FLOOR}")
    print("=" * 90)

    # Check data availability
    print("\n[Step 1] Data availability check:")
    available = []
    for sym in CRYPTO_SYMBOLS:
        counts = {}
        for tf in TIMEFRAMES:
            df = load_klines(sym, tf)
            counts[tf] = len(df) if df is not None else 0
        min_count = min(counts.values())
        status = "OK" if min_count >= MIN_BARS_FLOOR else "SKIP"
        print(f"  {sym:<20} {counts}  → {status}")
        if min_count >= MIN_BARS_FLOOR:
            available.append(sym)

    print(f"\n  Using {len(available)} symbols: {available}")

    # Step 2: Single-timeframe backtest
    print("\n[Step 2] Running single-timeframe backtests...")
    all_rows = []
    for tf in TIMEFRAMES:
        print(f"  Processing {tf}...")
        df_tf = run_single_tf(available, tf)
        all_rows.append(df_tf)
    single_tf_df = pd.concat(all_rows, ignore_index=True)
    print(f"  Total rows: {len(single_tf_df)}")

    # Print results per timeframe × direction
    for tf in TIMEFRAMES:
        for direction in ["LONG", "SHORT"]:
            agg = agg_by_filter(single_tf_df, tf, direction)
            print_table(agg, f"Timeframe: {tf} | Direction: {direction}")

    # Step 3: Cross-timeframe approval tests
    print("\n[Step 3] Running cross-timeframe approval tests...")
    cross_df = run_cross_tf(available)
    for direction in ["LONG", "SHORT"]:
        print_cross_tf_table(cross_df, direction)

    # Step 4: Consistency check
    print_consistency_check(single_tf_df)

    # Summary: Best filters per timeframe
    print(f"\n{'='*90}")
    print("  SUMMARY: Best filter per timeframe (by avg_gain, min 20 trades)")
    print(f"{'='*90}")
    for direction in ["LONG", "SHORT"]:
        print(f"\n  Direction: {direction}")
        print(f"  {'TF':<6} {'Best Filter':<30} {'Trades':>7} {'AvgGain%':>9} {'WinRate%':>9}")
        print("  " + "-" * 65)
        for tf in TIMEFRAMES:
            sub = single_tf_df[
                (single_tf_df["timeframe"] == tf)
                & (single_tf_df["direction"] == direction)
                & (single_tf_df["count"] >= 5)
            ]
            if sub.empty:
                continue
            agg = (
                sub.groupby("filter")
                .agg(total_trades=("count", "sum"), avg_gain=("avg_gain", "mean"), win_rate=("win_rate", "mean"))
                .reset_index()
            )
            agg = agg[agg["total_trades"] >= 20]
            if agg.empty:
                continue
            best = agg.loc[agg["avg_gain"].idxmax()]
            print(
                f"  {tf:<6} {best['filter']:<30} {int(best['total_trades']):>7} "
                f"{best['avg_gain']:>+9.3f} {best['win_rate']:>9.1f}"
            )

    print(f"\n{'='*90}")
    print("  Done.")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
