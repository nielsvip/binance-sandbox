"""
WaveTrend + Donchian Channel Entry Signal Analysis
===================================================
Loads real NPZ indicator data for 12+ symbols, identifies entry signals,
measures forward returns, and finds optimal entry score thresholds.

Output: clear results table showing signal quality vs forward returns.
"""

import numpy as np
import os
import sys
from collections import defaultdict

BASE_PATH = "/Users/niels/Documents/binance"
NPZ_DIR = os.path.join(BASE_PATH, "backtest_v8", "indicators")

SYMBOLS = [
    "AAPL", "NVDA", "MSFT", "TSLA", "XOM", "GLD", "JPM", "META",
    "CVX", "BA", "AVGO", "MU", "AMD", "AMZN", "GOOGL", "HD",
]

TFS = ["5m", "15m", "1h", "4h", "D"]
BARS_PER_HOUR = 12  # 5-min bars


def safe(arr, i):
    """Safe array access — returns NaN for out-of-bounds or NaN values."""
    if i < 0 or i >= len(arr):
        return np.nan
    v = arr[i]
    if np.isnan(v) or np.isinf(v):
        return np.nan
    return float(v)


def safe_int(arr, i):
    v = safe(arr, i)
    if np.isnan(v):
        return 0
    return int(v)


def load_symbol(sym):
    path = os.path.join(NPZ_DIR, f"{sym}.npz")
    if not os.path.exists(path):
        return None
    return dict(np.load(path, allow_pickle=True))


def compute_forward_returns(close_5m, i, horizons_bars):
    """Compute forward returns at multiple horizons."""
    results = {}
    entry_price = safe(close_5m, i)
    if np.isnan(entry_price) or entry_price <= 0:
        return None
    for h_bars, label in horizons_bars:
        future_price = safe(close_5m, i + h_bars)
        if np.isnan(future_price) or future_price <= 0:
            results[label] = np.nan
        else:
            results[label] = (future_price - entry_price) / entry_price * 100.0
    return results


def score_entry_long(d, i):
    """Score a LONG entry at bar i. Returns (score, components_dict)."""
    score = 0.0
    components = {}

    # === 1. HTF TREND ALIGNMENT (30 pts) ===
    htf_score = 0.0
    # 4h WT bullish (wt1 > wt2)
    wt1_4h = safe(d["wt1_4h"], i)
    wt2_4h = safe(d["wt2_4h"], i)
    if not np.isnan(wt1_4h) and not np.isnan(wt2_4h):
        if wt1_4h > wt2_4h:
            htf_score += 5.0
        if wt1_4h > wt2_4h and wt1_4h > 0:
            htf_score += 2.0
    # D WT bullish
    wt1_d = safe(d["wt1_D"], i)
    wt2_d = safe(d["wt2_D"], i)
    if not np.isnan(wt1_d) and not np.isnan(wt2_d):
        if wt1_d > wt2_d:
            htf_score += 5.0
        if wt1_d > wt2_d and wt1_d > 0:
            htf_score += 2.0
    # 4h DC position (closer to middle/upper = bullish trend)
    dc_pos_4h = safe(d["dc_position_4h"], i)
    if not np.isnan(dc_pos_4h):
        if dc_pos_4h > 0.5:
            htf_score += 3.0 * min((dc_pos_4h - 0.5) * 4, 1.0)
    # D DC position above basis = bullish
    dc_pos_d = safe(d["dc_position_D"], i)
    if not np.isnan(dc_pos_d):
        if dc_pos_d > 0.4:
            htf_score += 3.0 * min((dc_pos_d - 0.4) * 3, 1.0)
    # wt_structure_4h == 1 (making higher lows)
    wt_struct_4h = safe_int(d["wt_structure_4h"], i)
    if wt_struct_4h == 1:
        htf_score += 3.0
    wt_struct_d = safe_int(d["wt_structure_D"], i)
    if wt_struct_d == 1:
        htf_score += 3.0
    # Price above EMA200 on D
    close_d = safe(d["close_D"], i)
    ema200_d = safe(d["ema_200_D"], i)
    if not np.isnan(close_d) and not np.isnan(ema200_d) and ema200_d > 0:
        if close_d > ema200_d:
            htf_score += 4.0
    htf_score = min(htf_score, 30.0)
    components["htf_trend"] = htf_score
    score += htf_score

    # === 2. LTF TRIGGER (20 pts) ===
    ltf_score = 0.0
    # 15m WT bullish cross
    wt_cross_bull_15m = safe_int(d["wt_cross_bull_15m"], i)
    if wt_cross_bull_15m:
        ltf_score += 6.0
    # 5m WT bullish cross
    wt_cross_bull_5m = safe_int(d["wt_cross_bull_5m"], i)
    if wt_cross_bull_5m:
        ltf_score += 4.0
    # 1h WT bullish cross (strong trigger)
    wt_cross_bull_1h = safe_int(d["wt_cross_bull_1h"], i)
    if wt_cross_bull_1h:
        ltf_score += 5.0
    # WT velocity turning positive on 15m
    wt_vel_15m = safe(d["wt_velocity_15m"], i)
    if not np.isnan(wt_vel_15m) and wt_vel_15m > 0:
        ltf_score += 2.0 * min(wt_vel_15m / 5.0, 1.0)
    # WT velocity positive on 1h
    wt_vel_1h = safe(d["wt_velocity_1h"], i)
    if not np.isnan(wt_vel_1h) and wt_vel_1h > 0:
        ltf_score += 3.0 * min(wt_vel_1h / 3.0, 1.0)
    ltf_score = min(ltf_score, 20.0)
    components["ltf_trigger"] = ltf_score
    score += ltf_score

    # === 3. DIVERGENCE / MOMENTUM QUALITY (15 pts) ===
    div_score = 0.0
    # WT cross value rising (current cross at higher level than previous)
    wt_cross_rising_1h = safe_int(d["wt_cross_rising_1h"], i)
    if wt_cross_rising_1h:
        div_score += 5.0
    # WT divergence on 1h (bullish = 1)
    wt_div_1h = safe_int(d["wt_divergence_1h"], i)
    if wt_div_1h == 1:
        div_score += 5.0
        # Divergence strength
        div_str = safe(d["wt_divergence_strength_1h"], i)
        if not np.isnan(div_str):
            div_score += 3.0 * div_str
    # WT higher-low count across TFs
    hl_count = safe_int(d["wt_hl_count"], i)
    if hl_count >= 3:
        div_score += 2.0
    div_score = min(div_score, 15.0)
    components["divergence"] = div_score
    score += div_score

    # === 4. DC POSITION — BUY THE DIP (15 pts) ===
    dc_score = 0.0
    # 1h DC position near low = buying dip in uptrend
    dc_pos_1h = safe(d["dc_position_1h"], i)
    if not np.isnan(dc_pos_1h):
        if dc_pos_1h < 0.3:
            # Near channel bottom — potential dip buy
            dc_score += 5.0 * (1.0 - dc_pos_1h / 0.3)
        elif dc_pos_1h > 0.7:
            # Near channel top = breakout momentum
            dc_score += 3.0 * min((dc_pos_1h - 0.7) / 0.3, 1.0)
    # 4h DC near bottom with HTF bullish = strong mean reversion
    if not np.isnan(dc_pos_4h) and dc_pos_4h < 0.3:
        if htf_score > 15:
            dc_score += 5.0 * (1.0 - dc_pos_4h / 0.3)
    # DC basis crossover on 15m (price crossing above mid-channel)
    dc_basis_xo_15m = safe_int(d["dc_basis_crossover_15m"], i)
    if dc_basis_xo_15m:
        dc_score += 3.0
    # DC low crossover on 1h (bouncing off channel low)
    dc_low_xo_1h = safe_int(d["dc_low_crossover_1h"], i)
    if dc_low_xo_1h:
        dc_score += 2.0
    dc_score = min(dc_score, 15.0)
    components["dc_position"] = dc_score
    score += dc_score

    # === 5. BB BREAKOUT (10 pts) ===
    bb_score = 0.0
    # BB %B > 1.0 on 4h = breakout above upper band
    bb_pctb_4h = safe(d["bb_pct_b_4h"], i)
    if not np.isnan(bb_pctb_4h) and abs(bb_pctb_4h) < 10:
        if bb_pctb_4h > 1.0:
            bb_score += 5.0 * min((bb_pctb_4h - 1.0) / 0.5, 1.0)
        elif bb_pctb_4h < 0.0:
            # Below lower band = oversold bounce potential
            bb_score += 2.0 * min(abs(bb_pctb_4h) / 0.3, 1.0)
    # BB %B > 1.0 on D = strong breakout
    bb_pctb_d = safe(d["bb_pct_b_D"], i)
    if not np.isnan(bb_pctb_d) and abs(bb_pctb_d) < 10:
        if bb_pctb_d > 1.0:
            bb_score += 5.0 * min((bb_pctb_d - 1.0) / 0.5, 1.0)
    bb_score = min(bb_score, 10.0)
    components["bb_breakout"] = bb_score
    score += bb_score

    # === 6. VOLUME + CONFIRMATION (10 pts) ===
    vol_score = 0.0
    # Relative volume on 1h
    rvol_1h = safe(d["relative_volume_1h"], i)
    if not np.isnan(rvol_1h) and rvol_1h > 0:
        if rvol_1h > 1.2:
            vol_score += 4.0 * min((rvol_1h - 1.0) / 1.0, 1.0)
    # MFI on 1h (money flow — not RSI)
    mfi_1h = safe(d["mfi_1h"], i)
    if not np.isnan(mfi_1h):
        if mfi_1h > 50:
            vol_score += 3.0 * min((mfi_1h - 50) / 30.0, 1.0)
        elif mfi_1h < 20:
            # Deeply oversold MFI = reversal potential
            vol_score += 2.0 * (1.0 - mfi_1h / 20.0)
    # Stoch K crossing up
    stoch_xo_15m = safe_int(d["stoch_crossover_15m"], i)
    if stoch_xo_15m:
        vol_score += 3.0
    vol_score = min(vol_score, 10.0)
    components["volume_confirm"] = vol_score
    score += vol_score

    return score, components


def score_entry_short(d, i):
    """Score a SHORT entry at bar i. Returns (score, components_dict)."""
    score = 0.0
    components = {}

    # === 1. HTF TREND ALIGNMENT (30 pts) — bearish ===
    htf_score = 0.0
    wt1_4h = safe(d["wt1_4h"], i)
    wt2_4h = safe(d["wt2_4h"], i)
    if not np.isnan(wt1_4h) and not np.isnan(wt2_4h):
        if wt1_4h < wt2_4h:
            htf_score += 5.0
        if wt1_4h < wt2_4h and wt1_4h < 0:
            htf_score += 2.0
    wt1_d = safe(d["wt1_D"], i)
    wt2_d = safe(d["wt2_D"], i)
    if not np.isnan(wt1_d) and not np.isnan(wt2_d):
        if wt1_d < wt2_d:
            htf_score += 5.0
        if wt1_d < wt2_d and wt1_d < 0:
            htf_score += 2.0
    dc_pos_4h = safe(d["dc_position_4h"], i)
    if not np.isnan(dc_pos_4h):
        if dc_pos_4h < 0.5:
            htf_score += 3.0 * min((0.5 - dc_pos_4h) * 4, 1.0)
    dc_pos_d = safe(d["dc_position_D"], i)
    if not np.isnan(dc_pos_d):
        if dc_pos_d < 0.6:
            htf_score += 3.0 * min((0.6 - dc_pos_d) * 3, 1.0)
    wt_struct_4h = safe_int(d["wt_structure_4h"], i)
    if wt_struct_4h == -1:
        htf_score += 3.0
    wt_struct_d = safe_int(d["wt_structure_D"], i)
    if wt_struct_d == -1:
        htf_score += 3.0
    close_d = safe(d["close_D"], i)
    ema200_d = safe(d["ema_200_D"], i)
    if not np.isnan(close_d) and not np.isnan(ema200_d) and ema200_d > 0:
        if close_d < ema200_d:
            htf_score += 4.0
    htf_score = min(htf_score, 30.0)
    components["htf_trend"] = htf_score
    score += htf_score

    # === 2. LTF TRIGGER (20 pts) — bearish ===
    ltf_score = 0.0
    wt_cross_bear_15m = safe_int(d["wt_cross_bear_15m"], i)
    if wt_cross_bear_15m:
        ltf_score += 6.0
    wt_cross_bear_5m = safe_int(d["wt_cross_bear_5m"], i)
    if wt_cross_bear_5m:
        ltf_score += 4.0
    wt_cross_bear_1h = safe_int(d["wt_cross_bear_1h"], i)
    if wt_cross_bear_1h:
        ltf_score += 5.0
    wt_vel_15m = safe(d["wt_velocity_15m"], i)
    if not np.isnan(wt_vel_15m) and wt_vel_15m < 0:
        ltf_score += 2.0 * min(abs(wt_vel_15m) / 5.0, 1.0)
    wt_vel_1h = safe(d["wt_velocity_1h"], i)
    if not np.isnan(wt_vel_1h) and wt_vel_1h < 0:
        ltf_score += 3.0 * min(abs(wt_vel_1h) / 3.0, 1.0)
    ltf_score = min(ltf_score, 20.0)
    components["ltf_trigger"] = ltf_score
    score += ltf_score

    # === 3. DIVERGENCE (15 pts) — bearish ===
    div_score = 0.0
    wt_cross_rising_1h = safe_int(d["wt_cross_rising_1h"], i)
    if not wt_cross_rising_1h:
        div_score += 5.0
    wt_div_1h = safe_int(d["wt_divergence_1h"], i)
    if wt_div_1h == -1:
        div_score += 5.0
        div_str = safe(d["wt_divergence_strength_1h"], i)
        if not np.isnan(div_str):
            div_score += 3.0 * div_str
    ll_count = safe_int(d["wt_ll_count"], i)
    if ll_count >= 3:
        div_score += 2.0
    div_score = min(div_score, 15.0)
    components["divergence"] = div_score
    score += div_score

    # === 4. DC POSITION — SELL THE RALLY (15 pts) ===
    dc_score = 0.0
    dc_pos_1h = safe(d["dc_position_1h"], i)
    if not np.isnan(dc_pos_1h):
        if dc_pos_1h > 0.7:
            dc_score += 5.0 * ((dc_pos_1h - 0.7) / 0.3)
        elif dc_pos_1h < 0.3:
            dc_score += 3.0 * min((0.3 - dc_pos_1h) / 0.3, 1.0)
    if not np.isnan(dc_pos_4h) and dc_pos_4h > 0.7:
        if htf_score > 15:
            dc_score += 5.0 * ((dc_pos_4h - 0.7) / 0.3)
    dc_basis_xu_15m = safe_int(d["dc_basis_crossunder_15m"], i)
    if dc_basis_xu_15m:
        dc_score += 3.0
    dc_high_xu_1h = safe_int(d["dc_high_crossunder_1h"], i)
    if dc_high_xu_1h:
        dc_score += 2.0
    dc_score = min(dc_score, 15.0)
    components["dc_position"] = dc_score
    score += dc_score

    # === 5. BB BREAKOUT (10 pts) — bearish ===
    bb_score = 0.0
    bb_pctb_4h = safe(d["bb_pct_b_4h"], i)
    if not np.isnan(bb_pctb_4h) and abs(bb_pctb_4h) < 10:
        if bb_pctb_4h < 0.0:
            bb_score += 5.0 * min(abs(bb_pctb_4h) / 0.5, 1.0)
        elif bb_pctb_4h > 1.0:
            bb_score += 2.0 * min((bb_pctb_4h - 1.0) / 0.3, 1.0)
    bb_pctb_d = safe(d["bb_pct_b_D"], i)
    if not np.isnan(bb_pctb_d) and abs(bb_pctb_d) < 10:
        if bb_pctb_d < 0.0:
            bb_score += 5.0 * min(abs(bb_pctb_d) / 0.5, 1.0)
    bb_score = min(bb_score, 10.0)
    components["bb_breakout"] = bb_score
    score += bb_score

    # === 6. VOLUME + CONFIRMATION (10 pts) ===
    vol_score = 0.0
    rvol_1h = safe(d["relative_volume_1h"], i)
    if not np.isnan(rvol_1h) and rvol_1h > 0:
        if rvol_1h > 1.2:
            vol_score += 4.0 * min((rvol_1h - 1.0) / 1.0, 1.0)
    mfi_1h = safe(d["mfi_1h"], i)
    if not np.isnan(mfi_1h):
        if mfi_1h < 50:
            vol_score += 3.0 * min((50 - mfi_1h) / 30.0, 1.0)
        elif mfi_1h > 80:
            vol_score += 2.0 * ((mfi_1h - 80) / 20.0)
    stoch_xu_15m = safe_int(d["stoch_crossunder_15m"], i)
    if stoch_xu_15m:
        vol_score += 3.0
    vol_score = min(vol_score, 10.0)
    components["volume_confirm"] = vol_score
    score += vol_score

    return score, components


def analyze_signals():
    """Main analysis: find entry signals, measure forward returns, find optimal thresholds."""
    print("=" * 100)
    print("WaveTrend + Donchian Channel Entry Signal Analysis")
    print("=" * 100)
    print()

    horizons = [
        (BARS_PER_HOUR * 1, "1h"),
        (BARS_PER_HOUR * 2, "2h"),
        (BARS_PER_HOUR * 4, "4h"),
        (BARS_PER_HOUR * 8, "8h"),
    ]

    # Collect all scored entries across symbols
    all_long_entries = []
    all_short_entries = []
    per_symbol_stats = {}

    for sym in SYMBOLS:
        d = load_symbol(sym)
        if d is None:
            print(f"  SKIP {sym} — NPZ not found")
            continue
        close_5m = d["close_5m"]
        n = len(close_5m)
        # Skip warmup (first 5000 bars ~ 26 days) and leave room for forward returns
        start_bar = 5000
        end_bar = n - BARS_PER_HOUR * 10

        if end_bar <= start_bar:
            print(f"  SKIP {sym} — not enough data ({n} bars)")
            continue

        sym_longs = 0
        sym_shorts = 0

        # Scan for entry signals: WT cross on 1h or 15m as trigger
        for i in range(start_bar, end_bar):
            # LONG: check for bullish WT cross on 15m or 1h
            has_bull_trigger = (
                safe_int(d["wt_cross_bull_1h"], i)
                or safe_int(d["wt_cross_bull_15m"], i)
            )
            if has_bull_trigger:
                score, comp = score_entry_long(d, i)
                fwd = compute_forward_returns(close_5m, i, horizons)
                if fwd is not None:
                    all_long_entries.append((sym, i, score, comp, fwd))
                    sym_longs += 1

            # SHORT: check for bearish WT cross on 15m or 1h
            has_bear_trigger = (
                safe_int(d["wt_cross_bear_1h"], i)
                or safe_int(d["wt_cross_bear_15m"], i)
            )
            if has_bear_trigger:
                score, comp = score_entry_short(d, i)
                fwd = compute_forward_returns(close_5m, i, horizons)
                if fwd is not None:
                    all_short_entries.append((sym, i, score, comp, fwd))
                    sym_shorts += 1

        bars_scanned = end_bar - start_bar
        months = bars_scanned / (BARS_PER_HOUR * 6.5 * 21)  # ~21 trading days/mo, 6.5h/day
        per_symbol_stats[sym] = {
            "longs": sym_longs,
            "shorts": sym_shorts,
            "months": months,
            "longs_per_mo": sym_longs / max(months, 1),
            "shorts_per_mo": sym_shorts / max(months, 1),
        }
        print(f"  {sym}: {sym_longs} long signals, {sym_shorts} short signals over {months:.1f} months")

    print()
    print(f"Total LONG signals: {len(all_long_entries)}")
    print(f"Total SHORT signals: {len(all_short_entries)}")
    print()

    # === ANALYZE LONG ENTRIES BY SCORE THRESHOLD ===
    print("=" * 100)
    print("LONG ENTRY ANALYSIS — Score Threshold vs Forward Returns")
    print("=" * 100)
    print()
    analyze_direction(all_long_entries, "LONG", horizons, is_long=True)

    # === ANALYZE SHORT ENTRIES BY SCORE THRESHOLD ===
    print()
    print("=" * 100)
    print("SHORT ENTRY ANALYSIS — Score Threshold vs Forward Returns")
    print("=" * 100)
    print()
    analyze_direction(all_short_entries, "SHORT", horizons, is_long=False)

    # === COMPONENT IMPORTANCE ANALYSIS ===
    print()
    print("=" * 100)
    print("COMPONENT IMPORTANCE — Which factors predict winners?")
    print("=" * 100)
    print()
    analyze_components(all_long_entries, "LONG", is_long=True)
    print()
    analyze_components(all_short_entries, "SHORT", is_long=False)

    # === PER-SYMBOL BREAKDOWN ===
    print()
    print("=" * 100)
    print("PER-SYMBOL SIGNAL FREQUENCY")
    print("=" * 100)
    print(f"{'Symbol':<8} {'Long/mo':>8} {'Short/mo':>8} {'Total/mo':>9} {'Months':>7}")
    print("-" * 44)
    for sym in sorted(per_symbol_stats.keys()):
        s = per_symbol_stats[sym]
        print(f"{sym:<8} {s['longs_per_mo']:>8.1f} {s['shorts_per_mo']:>8.1f} {s['longs_per_mo']+s['shorts_per_mo']:>9.1f} {s['months']:>7.1f}")

    # === BEST ENTRIES EXAMPLES ===
    print()
    print("=" * 100)
    print("TOP 20 HIGHEST-SCORED LONG ENTRIES")
    print("=" * 100)
    print_top_entries(all_long_entries, is_long=True, n=20)

    print()
    print("=" * 100)
    print("TOP 20 HIGHEST-SCORED SHORT ENTRIES")
    print("=" * 100)
    print_top_entries(all_short_entries, is_long=False, n=20)

    # === OPTIMAL THRESHOLD RECOMMENDATION ===
    print()
    print("=" * 100)
    print("OPTIMAL THRESHOLD RECOMMENDATION")
    print("=" * 100)
    find_optimal_threshold(all_long_entries, "LONG", is_long=True)
    find_optimal_threshold(all_short_entries, "SHORT", is_long=False)


def analyze_direction(entries, direction, horizons, is_long):
    """Analyze entries at various score thresholds."""
    if not entries:
        print(f"  No {direction} entries found.")
        return

    thresholds = [0, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70]
    header = f"{'Thresh':>6} {'Count':>7} {'WR_1h':>7} {'WR_2h':>7} {'WR_4h':>7} {'WR_8h':>7} "
    header += f"{'Avg_1h':>8} {'Avg_4h':>8} {'Avg_8h':>8} "
    header += f"{'AvgW_4h':>8} {'AvgL_4h':>8} {'PF_4h':>7} {'Sharpe':>7}"
    print(header)
    print("-" * len(header))

    for thresh in thresholds:
        filtered = [(s, i, sc, comp, fwd) for s, i, sc, comp, fwd in entries if sc >= thresh]
        if len(filtered) < 20:
            continue

        wr = {}
        avg_ret = {}
        for _, label in horizons:
            returns = []
            for _, _, _, _, fwd in filtered:
                r = fwd.get(label, np.nan)
                if not np.isnan(r):
                    if not is_long:
                        r = -r
                    returns.append(r)
            if returns:
                returns_arr = np.array(returns)
                wins = np.sum(returns_arr > 0.3) / len(returns_arr) * 100
                wr[label] = wins
                avg_ret[label] = np.mean(returns_arr)
            else:
                wr[label] = 0
                avg_ret[label] = 0

        # Compute PF and Sharpe for 4h horizon
        returns_4h = []
        for _, _, _, _, fwd in filtered:
            r = fwd.get("4h", np.nan)
            if not np.isnan(r):
                if not is_long:
                    r = -r
                returns_4h.append(r)
        returns_4h = np.array(returns_4h)
        winners_4h = returns_4h[returns_4h > 0]
        losers_4h = returns_4h[returns_4h < 0]
        avg_w = np.mean(winners_4h) if len(winners_4h) > 0 else 0
        avg_l = np.mean(losers_4h) if len(losers_4h) > 0 else 0
        pf = abs(np.sum(winners_4h) / np.sum(losers_4h)) if len(losers_4h) > 0 and np.sum(losers_4h) != 0 else 99.9
        sharpe = np.mean(returns_4h) / np.std(returns_4h) * np.sqrt(252) if np.std(returns_4h) > 0 else 0

        row = f"{thresh:>6} {len(filtered):>7} "
        row += f"{wr.get('1h', 0):>6.1f}% {wr.get('2h', 0):>6.1f}% {wr.get('4h', 0):>6.1f}% {wr.get('8h', 0):>6.1f}% "
        row += f"{avg_ret.get('1h', 0):>7.3f}% {avg_ret.get('4h', 0):>7.3f}% {avg_ret.get('8h', 0):>7.3f}% "
        row += f"{avg_w:>7.3f}% {avg_l:>7.3f}% {min(pf, 99.9):>6.2f}x {sharpe:>7.2f}"
        print(row)


def analyze_components(entries, direction, is_long):
    """Analyze which score components predict winners vs losers (4h horizon)."""
    if not entries:
        return

    print(f"\n{direction} — Component averages for WINNERS vs LOSERS (4h, >0.3% threshold):")
    comp_names = ["htf_trend", "ltf_trigger", "divergence", "dc_position", "bb_breakout", "volume_confirm"]

    winners = []
    losers = []
    for _, _, _, comp, fwd in entries:
        r = fwd.get("4h", np.nan)
        if np.isnan(r):
            continue
        if not is_long:
            r = -r
        if r > 0.3:
            winners.append(comp)
        elif r < -0.3:
            losers.append(comp)

    if not winners or not losers:
        print("  Not enough data.")
        return

    print(f"  {'Component':<18} {'Winners':>10} {'Losers':>10} {'Delta':>10} {'Importance':>12}")
    print(f"  {'-'*62}")
    for cn in comp_names:
        w_avg = np.mean([c.get(cn, 0) for c in winners])
        l_avg = np.mean([c.get(cn, 0) for c in losers])
        delta = w_avg - l_avg
        # Normalized importance
        max_pts = {"htf_trend": 30, "ltf_trigger": 20, "divergence": 15, "dc_position": 15, "bb_breakout": 10, "volume_confirm": 10}
        importance = delta / max_pts.get(cn, 1) * 100
        print(f"  {cn:<18} {w_avg:>10.2f} {l_avg:>10.2f} {delta:>+10.2f} {importance:>+11.1f}%")


def print_top_entries(entries, is_long, n=20):
    """Print top N entries by score."""
    sorted_entries = sorted(entries, key=lambda x: x[2], reverse=True)[:n]
    print(f"{'Sym':<6} {'Bar':>8} {'Score':>6} {'HTF':>5} {'LTF':>5} {'Div':>5} {'DC':>5} {'BB':>5} {'Vol':>5} | {'1h':>7} {'4h':>7} {'8h':>7}")
    print("-" * 90)
    for sym, bar, score, comp, fwd in sorted_entries:
        r1h = fwd.get("1h", np.nan)
        r4h = fwd.get("4h", np.nan)
        r8h = fwd.get("8h", np.nan)
        if not is_long:
            r1h = -r1h if not np.isnan(r1h) else np.nan
            r4h = -r4h if not np.isnan(r4h) else np.nan
            r8h = -r8h if not np.isnan(r8h) else np.nan
        print(
            f"{sym:<6} {bar:>8} {score:>6.1f} "
            f"{comp.get('htf_trend', 0):>5.1f} {comp.get('ltf_trigger', 0):>5.1f} "
            f"{comp.get('divergence', 0):>5.1f} {comp.get('dc_position', 0):>5.1f} "
            f"{comp.get('bb_breakout', 0):>5.1f} {comp.get('volume_confirm', 0):>5.1f} | "
            f"{r1h:>6.2f}% {r4h:>6.2f}% {r8h:>6.2f}%"
        )


def find_optimal_threshold(entries, direction, is_long):
    """Find the score threshold that maximizes risk-adjusted returns."""
    if not entries:
        return

    best_sharpe = -999
    best_thresh = 0
    best_wr = 0
    best_pf = 0
    best_count = 0

    for thresh in range(10, 76, 1):
        filtered = [e for e in entries if e[2] >= thresh]
        if len(filtered) < 30:
            continue

        returns_4h = []
        for _, _, _, _, fwd in filtered:
            r = fwd.get("4h", np.nan)
            if not np.isnan(r):
                if not is_long:
                    r = -r
                returns_4h.append(r)

        if len(returns_4h) < 30:
            continue

        returns_4h = np.array(returns_4h)
        mean_r = np.mean(returns_4h)
        std_r = np.std(returns_4h)
        if std_r == 0:
            continue

        sharpe = mean_r / std_r * np.sqrt(252)
        wr = np.sum(returns_4h > 0.3) / len(returns_4h) * 100
        winners = returns_4h[returns_4h > 0]
        losers = returns_4h[returns_4h < 0]
        pf = abs(np.sum(winners) / np.sum(losers)) if len(losers) > 0 and np.sum(losers) != 0 else 99.9

        # Composite metric: sharpe * sqrt(count) to balance quality and quantity
        composite = sharpe * np.sqrt(len(filtered)) / 100

        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_thresh = thresh
            best_wr = wr
            best_pf = pf
            best_count = len(filtered)

    print(f"\n  {direction} Optimal Threshold: >= {best_thresh}")
    print(f"  Signals: {best_count}")
    print(f"  Win Rate (4h, >0.3%): {best_wr:.1f}%")
    print(f"  Profit Factor (4h): {best_pf:.2f}x")
    print(f"  Sharpe (4h): {best_sharpe:.2f}")


def validate_production_scorer():
    """Validate the production scorer (wt_dc_entry_scorer.py) against real NPZ data."""
    from wt_dc_entry_scorer import score_entry
    print()
    print("=" * 100)
    print("PRODUCTION SCORER VALIDATION (wt_dc_entry_scorer.py)")
    print("=" * 100)
    print()

    all_long = []
    all_short = []

    for sym in SYMBOLS:
        d = load_symbol(sym)
        if d is None:
            continue
        close_5m = d["close_5m"]
        n = len(close_5m)
        start_bar = 5000
        end_bar = n - BARS_PER_HOUR * 10
        if end_bar <= start_bar:
            continue
        for i in range(start_bar, end_bar):
            if d["wt_cross_bull_1h"][i] == 1 or d["wt_cross_bull_15m"][i] == 1:
                ind = {k: float(d[k][i]) for k in d if i < len(d[k])}
                sc, _ = score_entry(ind, is_long=True)
                ep = float(close_5m[i])
                fp = float(close_5m[min(i + BARS_PER_HOUR * 4, n - 1)])
                if ep > 0 and fp > 0:
                    all_long.append((sc, (fp - ep) / ep * 100))
            if d["wt_cross_bear_1h"][i] == 1 or d["wt_cross_bear_15m"][i] == 1:
                ind = {k: float(d[k][i]) for k in d if i < len(d[k])}
                sc, _ = score_entry(ind, is_long=False)
                ep = float(close_5m[i])
                fp = float(close_5m[min(i + BARS_PER_HOUR * 4, n - 1)])
                if ep > 0 and fp > 0:
                    all_short.append((sc, -(fp - ep) / ep * 100))

    for direction, entries in [("LONG", all_long), ("SHORT", all_short)]:
        print(f"--- {direction} ({len(entries)} signals) ---")
        print(f"{'Thresh':>6} {'Count':>7} {'WR':>7} {'AvgRet':>8} {'PF':>7} {'Sharpe':>7}")
        print("-" * 50)
        for thresh in [35, 40, 45, 50, 55, 60, 65]:
            rets = np.array([r for s, r in entries if s >= thresh])
            if len(rets) < 30:
                continue
            wr = np.sum(rets > 0.3) / len(rets) * 100
            wins = rets[rets > 0]
            losses = rets[rets < 0]
            pf = abs(np.sum(wins) / np.sum(losses)) if len(losses) > 0 and np.sum(losses) != 0 else 99.9
            sharpe = np.mean(rets) / np.std(rets) * np.sqrt(252) if np.std(rets) > 0 else 0
            print(f"{thresh:>6} {len(rets):>7} {wr:>6.1f}% {np.mean(rets):>7.3f}% {min(pf, 99.9):>6.2f}x {sharpe:>7.2f}")
        print()


if __name__ == "__main__":
    analyze_signals()
    validate_production_scorer()
