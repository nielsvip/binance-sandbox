#!/usr/bin/env python3
"""
WT/DC Metric Research Engine
=============================
Phase 1: Analyze ALL WT and DC metrics across ALL timeframes for predictive power
Phase 2: Build composite entry/exit signals from best metrics
Phase 3: Backtest across all 48 crypto NPZ files

This is RESEARCH code - results must be validated through V5 engine before live use.
"""
import numpy as np
import os
import sys
import json
from collections import defaultdict
from datetime import datetime

NPZ_DIR = "backtest_v4/indicators"
RESULTS_DIR = "data/wt_dc_research"
TFS = ["3m", "15m", "1h", "4h", "D"]

# Forward return horizons (in bars at 3m base)
# V4 NPZ base is 15m, so 4 bars = 1h, 16 bars = 4h, 96 bars = 1D
HORIZONS = {"1h": 4, "4h": 16, "1D": 96}

# ------------------------------------------------------------------ #
#  UTILITY
# ------------------------------------------------------------------ #
def safe_sharpe(returns, periods_per_year=35040):
    """Annualized Sharpe from array of per-bar returns. 35040 = 15m bars/year."""
    if len(returns) < 100 or np.std(returns) == 0:
        return 0.0
    return float(np.mean(returns) / np.std(returns) * np.sqrt(periods_per_year))


def max_drawdown(equity):
    """Max drawdown from equity curve."""
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1)
    return float(np.min(dd))


def profit_factor(pnl_array):
    """Profit factor from array of trade PnLs."""
    gains = pnl_array[pnl_array > 0].sum()
    losses = abs(pnl_array[pnl_array < 0].sum())
    if losses == 0:
        return 999.0 if gains > 0 else 0.0
    return float(gains / losses)


# ------------------------------------------------------------------ #
#  PHASE 1: Metric Predictive Power Analysis
# ------------------------------------------------------------------ #
def analyze_metric_power(npz_dir):
    """For each WT/DC metric, compute correlation with forward returns across all symbols."""
    files = sorted([f for f in os.listdir(npz_dir) if f.endswith(".npz")])
    if not files:
        print(f"No NPZ files in {npz_dir}")
        return {}

    print(f"\n{'='*80}")
    print(f"PHASE 1: Analyzing {len(files)} symbols for WT/DC predictive power")
    print(f"{'='*80}\n")

    # Collect per-metric correlations across all symbols
    metric_corrs = defaultdict(lambda: defaultdict(list))  # metric -> horizon -> [corrs]
    metric_edge = defaultdict(lambda: defaultdict(list))   # metric -> state -> [fwd_returns]

    for fi, fname in enumerate(files):
        sym = fname.replace(".npz", "")
        d = np.load(os.path.join(npz_dir, fname), allow_pickle=True)
        keys = list(d.keys())

        # Get close prices for forward returns
        close = d["close"].astype(np.float64)
        n = len(close)

        # Compute forward returns at each horizon
        fwd_rets = {}
        for hname, hbars in HORIZONS.items():
            fr = np.full(n, np.nan)
            fr[: n - hbars] = (close[hbars:] - close[:-hbars]) / np.where(
                close[:-hbars] > 0, close[:-hbars], 1
            )
            fwd_rets[hname] = fr

        # Get all WT and DC fields
        wt_keys = sorted([k for k in keys if "wt" in k])
        dc_keys = sorted([k for k in keys if k.startswith("dc")])
        target_keys = wt_keys + dc_keys

        for mk in target_keys:
            vals = d[mk].astype(np.float64)
            # Skip constant or near-constant arrays
            if np.std(vals[np.isfinite(vals)]) < 1e-10:
                continue

            for hname, fr in fwd_rets.items():
                mask = np.isfinite(vals) & np.isfinite(fr)
                if mask.sum() < 1000:
                    continue
                corr = np.corrcoef(vals[mask], fr[mask])[0, 1]
                if np.isfinite(corr):
                    metric_corrs[mk][hname].append(corr)

            # For binary/categorical metrics, compute conditional forward returns
            if d[mk].dtype == np.int8:
                unique_vals = np.unique(vals[np.isfinite(vals)])
                if len(unique_vals) <= 5:  # categorical
                    fr_4h = fwd_rets["4h"]
                    for uv in unique_vals:
                        mask = (vals == uv) & np.isfinite(fr_4h)
                        if mask.sum() > 100:
                            avg_ret = np.mean(fr_4h[mask])
                            metric_edge[mk][int(uv)].append(avg_ret)

        if (fi + 1) % 10 == 0 or fi == len(files) - 1:
            print(f"  Processed {fi+1}/{len(files)} symbols")

    # Aggregate: mean correlation across symbols
    results = {}
    print(f"\n{'='*80}")
    print("TOP PREDICTIVE METRICS (by mean |correlation| with 4h forward return)")
    print(f"{'='*80}")

    metric_scores = []
    for mk, horizons in metric_corrs.items():
        for hname, corrs in horizons.items():
            mean_corr = np.mean(corrs)
            std_corr = np.std(corrs)
            consistency = sum(1 for c in corrs if np.sign(c) == np.sign(mean_corr)) / len(corrs)
            metric_scores.append({
                "metric": mk,
                "horizon": hname,
                "mean_corr": mean_corr,
                "abs_corr": abs(mean_corr),
                "std_corr": std_corr,
                "consistency": consistency,
                "n_symbols": len(corrs),
            })

    metric_scores.sort(key=lambda x: x["abs_corr"], reverse=True)

    # Print top 50
    print(f"\n{'Metric':<40} {'Horizon':<6} {'MeanCorr':>10} {'Consistency':>12} {'Symbols':>8}")
    print("-" * 80)
    for ms in metric_scores[:50]:
        print(f"{ms['metric']:<40} {ms['horizon']:<6} {ms['mean_corr']:>10.4f} {ms['consistency']:>11.1%} {ms['n_symbols']:>8}")

    # Aggregate edge for binary metrics
    print(f"\n{'='*80}")
    print("CONDITIONAL EDGES (binary/categorical metrics, avg 4h return by state)")
    print(f"{'='*80}\n")

    edge_scores = []
    for mk, states in metric_edge.items():
        for state, rets in states.items():
            mean_ret = np.mean(rets)
            edge_scores.append({"metric": mk, "state": state, "mean_4h_ret": mean_ret, "n_symbols": len(rets)})

    edge_scores.sort(key=lambda x: abs(x["mean_4h_ret"]), reverse=True)
    print(f"{'Metric':<40} {'State':>6} {'Mean4hRet':>12} {'Symbols':>8}")
    print("-" * 70)
    for es in edge_scores[:40]:
        print(f"{es['metric']:<40} {es['state']:>6} {es['mean_4h_ret']:>11.4%} {es['n_symbols']:>8}")

    results["metric_scores"] = metric_scores[:100]
    results["edge_scores"] = edge_scores[:100]
    return results


# ------------------------------------------------------------------ #
#  PHASE 2: Build Entry/Exit Signals
# ------------------------------------------------------------------ #
def build_entry_long(d, cfg):
    """
    Composite LONG entry signal using ALL WT and DC metrics.
    Returns: score array (higher = stronger entry signal)
    """
    n = len(d["close"])
    score = np.zeros(n, dtype=np.float64)
    trigger = np.zeros(n, dtype=bool)

    # --- WT CROSS TRIGGERS (LTF) ---
    # 3m bull cross = primary trigger
    wt_cross_bull_3m = d.get("wt_cross_bull_3m", np.zeros(n, dtype=np.int8))
    wt_cross_bull_15m = d.get("wt_cross_bull_15m", np.zeros(n, dtype=np.int8))
    trigger |= (wt_cross_bull_3m == 1)
    trigger |= (wt_cross_bull_15m == 1)

    # --- DC BREAKOUT TRIGGERS ---
    # Price crossing above DC basis on any TF
    for tf in TFS:
        k = f"dc_basis_crossover_{tf}"
        if k in d:
            trigger |= (d[k] == 1)
        k2 = f"dc_low_crossover_{tf}"
        if k2 in d:
            trigger |= (d[k2] == 1)

    # --- SCORING: WT ALIGNMENT ---
    # How many TFs are bullish?
    wt_bull_align = d.get("wt_bull_alignment", np.zeros(n, dtype=np.int8)).astype(np.float64)
    score += wt_bull_align * cfg["w_bull_alignment"]

    # WT composite scores
    wt_comp_long = d.get("wt_composite_long", np.zeros(n, dtype=np.float32)).astype(np.float64)
    wt_comp_delta = d.get("wt_composite_delta", np.zeros(n, dtype=np.float32)).astype(np.float64)
    score += np.clip(wt_comp_long / 100, 0, 1) * cfg["w_composite_long"]
    score += np.clip(wt_comp_delta / 100, -1, 1) * cfg["w_composite_delta"]

    # --- SCORING: WT MOMENTUM ---
    for tf in TFS:
        # Velocity positive = momentum building
        vel_k = f"wt_velocity_{tf}"
        if vel_k in d:
            vel = d[vel_k].astype(np.float64)
            score += np.where(vel > 0, 1, 0) * cfg["w_velocity_pos"]

        # Acceleration positive = momentum accelerating
        acc_k = f"wt_acceleration_{tf}"
        if acc_k in d:
            acc = d[acc_k].astype(np.float64)
            score += np.where(acc > 0, 1, 0) * cfg["w_accel_pos"]

        # WT percentile < 30 = oversold (mean reversion entry)
        pct_k = f"wt_percentile_{tf}"
        if pct_k in d:
            pct = d[pct_k].astype(np.float64)
            score += np.where(pct < cfg["oversold_pct"], 2, 0) * cfg["w_oversold"]

        # WT zscore < -1 = deeply oversold
        zs_k = f"wt_zscore_{tf}"
        if zs_k in d:
            zs = d[zs_k].astype(np.float64)
            score += np.where(zs < -1.0, 1, 0) * cfg["w_zscore_deep"]

        # WT bullish state
        bull_k = f"wt_bullish_{tf}"
        if bull_k in d:
            score += d[bull_k].astype(np.float64) * cfg["w_bullish_tf"]

    # --- SCORING: WT STRUCTURE ---
    for tf in TFS:
        # Higher lows in troughs = uptrend structure
        ts_k = f"wt_trough_structure_{tf}"
        if ts_k in d:
            ts = d[ts_k].astype(np.float64)
            score += np.where(ts == 1, 2, 0) * cfg["w_hl_structure"]  # HL = 1

        # Peak structure HH = strong uptrend
        ps_k = f"wt_peak_structure_{tf}"
        if ps_k in d:
            ps = d[ps_k].astype(np.float64)
            score += np.where(ps == 1, 1, 0) * cfg["w_hh_structure"]  # HH = 1

    # --- SCORING: WT DIVERGENCE ---
    # Bullish divergence on any TF
    for tf in TFS:
        div_k = f"wt_divergence_{tf}"
        if div_k in d:
            div = d[div_k].astype(np.float64)
            # Bullish div = 1 or 3 (hidden bull)
            score += np.where((div == 1) | (div == 3), 3, 0) * cfg["w_bull_div"]

        # Divergence strength
        ds_k = f"wt_divergence_strength_{tf}"
        if ds_k in d:
            ds = d[ds_k].astype(np.float64)
            div_val = d.get(div_k, np.zeros(n, dtype=np.int8)).astype(np.float64)
            bull_mask = (div_val == 1) | (div_val == 3)
            score += np.where(bull_mask, ds * 0.1, 0) * cfg["w_div_strength"]

    # --- SCORING: DC POSITION ---
    for tf in TFS:
        pos_k = f"dc_position_{tf}"
        if pos_k in d:
            pos = d[pos_k].astype(np.float64)
            # Near bottom of channel = value entry
            score += np.where(pos < cfg["dc_low_threshold"], 2, 0) * cfg["w_dc_low"]
            # Near top but breaking out = momentum entry
            score += np.where(pos > cfg["dc_high_threshold"], 1, 0) * cfg["w_dc_breakout"]

        # DC width (volatility expansion)
        w_k = f"dc_width_{tf}"
        if w_k in d:
            w = d[w_k].astype(np.float64)
            median_w = np.nanmedian(w)
            if median_w > 0:
                score += np.where(w > median_w * 1.5, 1, 0) * cfg["w_dc_expansion"]

    # --- SCORING: CROSS-TF COMPOSITES ---
    # WT cross counts
    for k in ["wt_rising_cross_count"]:
        if k in d:
            score += d[k].astype(np.float64) * cfg["w_rising_crosses"]

    # WT HL count (higher low structure across TFs)
    if "wt_hl_count" in d:
        score += d["wt_hl_count"].astype(np.float64) * cfg["w_hl_count"]

    # WT HH count
    if "wt_hh_count" in d:
        score += d["wt_hh_count"].astype(np.float64) * cfg["w_hh_count"]

    # WT oversold TF count
    if "wt_oversold_tf_count" in d:
        score += d["wt_oversold_tf_count"].astype(np.float64) * cfg["w_oversold_tfs"]

    # --- SCORING: WT MOMENTUM STATE ---
    for tf in TFS:
        ms_k = f"wt_momentum_state_{tf}"
        if ms_k in d:
            ms = d[ms_k].astype(np.float64)
            # IMPULSE_UP = 1 (strong), EXHAUST_DOWN = -2 (reversal incoming = buy)
            score += np.where(ms == 1, 2, 0) * cfg["w_impulse_up"]
            score += np.where(ms == -2, 1, 0) * cfg["w_exhaust_down"]

    # --- SCORING: WT WAVE PHASE ---
    for tf in TFS:
        wp_k = f"wt_wave_phase_{tf}"
        if wp_k in d:
            wp = d[wp_k].astype(np.float64)
            score += np.where(wp == 1, 1, 0) * cfg["w_wave_phase_bull"]

    # --- SCORING: WT SCORE (wt1-wt2 diff) ---
    for tf in TFS:
        ws_k = f"wt_score_{tf}"
        if ws_k in d:
            ws = d[ws_k].astype(np.float64)
            score += np.clip(ws / 20, -1, 1) * cfg["w_wt_score"]

    # --- SCORING: WT CROSS QUALITY ---
    for tf in TFS:
        cv_k = f"wt_cross_value_{tf}"
        cr_k = f"wt_cross_rising_{tf}"
        cb_k = f"wt_cross_bull_{tf}"
        if cv_k in d and cb_k in d:
            cv = d[cv_k].astype(np.float64)
            cb = d[cb_k].astype(np.float64)
            # Bull cross at low WT value = higher quality
            score += np.where((cb == 1) & (cv < -30), 3, 0) * cfg["w_deep_cross"]
            score += np.where((cb == 1) & (cv < 0), 1, 0) * cfg["w_neg_cross"]
        if cr_k in d and cb_k in d:
            cr = d[cr_k].astype(np.float64)
            cb2 = d[cb_k].astype(np.float64)
            # Rising bull cross = trend continuation
            score += np.where((cb2 == 1) & (cr == 1), 2, 0) * cfg["w_rising_bull_cross"]

    # --- SCORING: WT PEAK/TROUGH LEVELS ---
    for tf in TFS:
        tr_k = f"wt_trough_{tf}"
        trp_k = f"wt_trough_prev_{tf}"
        if tr_k in d and trp_k in d:
            tr = d[tr_k].astype(np.float64)
            trp = d[trp_k].astype(np.float64)
            # Current trough higher than previous = HL
            score += np.where((tr > trp) & (trp < 0), 2, 0) * cfg["w_trough_hl"]

    # Final: entry signal = trigger AND score above threshold
    entry = trigger & (score >= cfg["entry_threshold"])
    return entry, score


def build_entry_short(d, cfg):
    """Composite SHORT entry signal — mirror of long."""
    n = len(d["close"])
    score = np.zeros(n, dtype=np.float64)
    trigger = np.zeros(n, dtype=bool)

    # Bear cross triggers
    wt_cross_bear_3m = d.get("wt_cross_bear_3m", np.zeros(n, dtype=np.int8))
    wt_cross_bear_15m = d.get("wt_cross_bear_15m", np.zeros(n, dtype=np.int8))
    trigger |= (wt_cross_bear_3m == 1)
    trigger |= (wt_cross_bear_15m == 1)

    for tf in TFS:
        k = f"dc_basis_crossunder_{tf}"
        if k in d:
            trigger |= (d[k] == 1)
        k2 = f"dc_high_crossunder_{tf}"
        if k2 in d:
            trigger |= (d[k2] == 1)

    # Bear alignment
    wt_bear_align = d.get("wt_bear_alignment", np.zeros(n, dtype=np.int8)).astype(np.float64)
    score += wt_bear_align * cfg["w_bull_alignment"]

    wt_comp_short = d.get("wt_composite_short", np.zeros(n, dtype=np.float32)).astype(np.float64)
    score += np.clip(wt_comp_short / 100, 0, 1) * cfg["w_composite_long"]
    score += np.clip(-d.get("wt_composite_delta", np.zeros(n, dtype=np.float32)).astype(np.float64) / 100, -1, 1) * cfg["w_composite_delta"]

    for tf in TFS:
        vel_k = f"wt_velocity_{tf}"
        if vel_k in d:
            vel = d[vel_k].astype(np.float64)
            score += np.where(vel < 0, 1, 0) * cfg["w_velocity_pos"]

        acc_k = f"wt_acceleration_{tf}"
        if acc_k in d:
            acc = d[acc_k].astype(np.float64)
            score += np.where(acc < 0, 1, 0) * cfg["w_accel_pos"]

        pct_k = f"wt_percentile_{tf}"
        if pct_k in d:
            pct = d[pct_k].astype(np.float64)
            score += np.where(pct > (100 - cfg["oversold_pct"]), 2, 0) * cfg["w_oversold"]

        zs_k = f"wt_zscore_{tf}"
        if zs_k in d:
            zs = d[zs_k].astype(np.float64)
            score += np.where(zs > 1.0, 1, 0) * cfg["w_zscore_deep"]

        bull_k = f"wt_bullish_{tf}"
        if bull_k in d:
            score += (1 - d[bull_k].astype(np.float64)) * cfg["w_bullish_tf"]

        ts_k = f"wt_peak_structure_{tf}"
        if ts_k in d:
            ts = d[ts_k].astype(np.float64)
            score += np.where(ts == -1, 2, 0) * cfg["w_hl_structure"]  # LH = -1

        ps_k = f"wt_trough_structure_{tf}"
        if ps_k in d:
            ps = d[ps_k].astype(np.float64)
            score += np.where(ps == -1, 1, 0) * cfg["w_hh_structure"]  # LL = -1

        div_k = f"wt_divergence_{tf}"
        if div_k in d:
            div = d[div_k].astype(np.float64)
            score += np.where((div == -1) | (div == -3), 3, 0) * cfg["w_bull_div"]

        ms_k = f"wt_momentum_state_{tf}"
        if ms_k in d:
            ms = d[ms_k].astype(np.float64)
            score += np.where(ms == -1, 2, 0) * cfg["w_impulse_up"]  # IMPULSE_DOWN
            score += np.where(ms == 2, 1, 0) * cfg["w_exhaust_down"]  # EXHAUST_UP

        wp_k = f"wt_wave_phase_{tf}"
        if wp_k in d:
            wp = d[wp_k].astype(np.float64)
            score += np.where(wp == -1, 1, 0) * cfg["w_wave_phase_bull"]

        ws_k = f"wt_score_{tf}"
        if ws_k in d:
            ws = d[ws_k].astype(np.float64)
            score += np.clip(-ws / 20, -1, 1) * cfg["w_wt_score"]

        cv_k = f"wt_cross_value_{tf}"
        cb_k = f"wt_cross_bear_{tf}"
        if cv_k in d and cb_k in d:
            cv = d[cv_k].astype(np.float64)
            cb = d[cb_k].astype(np.float64)
            score += np.where((cb == 1) & (cv > 30), 3, 0) * cfg["w_deep_cross"]
            score += np.where((cb == 1) & (cv > 0), 1, 0) * cfg["w_neg_cross"]

    for tf in TFS:
        pos_k = f"dc_position_{tf}"
        if pos_k in d:
            pos = d[pos_k].astype(np.float64)
            score += np.where(pos > (1 - cfg["dc_low_threshold"]), 2, 0) * cfg["w_dc_low"]
            score += np.where(pos < (1 - cfg["dc_high_threshold"]), 1, 0) * cfg["w_dc_breakout"]

    if "wt_ll_count" in d:
        score += d["wt_ll_count"].astype(np.float64) * cfg["w_hl_count"]

    if "wt_overbought_tf_count" in d:
        score += d["wt_overbought_tf_count"].astype(np.float64) * cfg["w_oversold_tfs"]

    entry = trigger & (score >= cfg["entry_threshold"])
    return entry, score


def build_exit_long(d, cfg):
    """Exit signal for LONG positions using WT/DC metrics."""
    n = len(d["close"])
    score = np.zeros(n, dtype=np.float64)

    # --- WT BEAR CROSSES ---
    for tf in TFS:
        cb_k = f"wt_cross_bear_{tf}"
        if cb_k in d:
            cb = d[cb_k].astype(np.float64)
            weight = cfg["exit_cross_weights"].get(tf, 1.0)
            score += cb * weight

    # --- WT OVERBOUGHT + TURNING ---
    for tf in TFS:
        pct_k = f"wt_percentile_{tf}"
        vel_k = f"wt_velocity_{tf}"
        if pct_k in d and vel_k in d:
            pct = d[pct_k].astype(np.float64)
            vel = d[vel_k].astype(np.float64)
            # Overbought AND velocity turning negative
            score += np.where((pct > cfg["exit_overbought_pct"]) & (vel < 0), 2, 0) * cfg["w_exit_ob_turn"]

    # --- WT STRUCTURE BREAKDOWN ---
    for tf in TFS:
        ps_k = f"wt_peak_structure_{tf}"
        if ps_k in d:
            ps = d[ps_k].astype(np.float64)
            # Lower high = trend weakening
            score += np.where(ps == -1, 2, 0) * cfg["w_exit_lh"]

        ts_k = f"wt_trough_structure_{tf}"
        if ts_k in d:
            ts = d[ts_k].astype(np.float64)
            # Lower low = trend broken
            score += np.where(ts == -1, 3, 0) * cfg["w_exit_ll"]

    # --- WT BEARISH DIVERGENCE ---
    for tf in TFS:
        div_k = f"wt_divergence_{tf}"
        if div_k in d:
            div = d[div_k].astype(np.float64)
            score += np.where((div == -1) | (div == -3), 3, 0) * cfg["w_exit_bear_div"]

    # --- WT MOMENTUM STATE ---
    for tf in TFS:
        ms_k = f"wt_momentum_state_{tf}"
        if ms_k in d:
            ms = d[ms_k].astype(np.float64)
            # EXHAUST_UP = momentum dying
            score += np.where(ms == 2, 2, 0) * cfg["w_exit_exhaust"]
            # IMPULSE_DOWN = strong reversal
            score += np.where(ms == -1, 3, 0) * cfg["w_exit_impulse_against"]

    # --- DC CHANNEL SIGNALS ---
    for tf in TFS:
        # Price crossing below DC basis
        k = f"dc_basis_crossunder_{tf}"
        if k in d:
            score += d[k].astype(np.float64) * cfg["w_exit_dc_basis_cross"]

        # DC position falling from top
        pos_k = f"dc_position_{tf}"
        if pos_k in d:
            pos = d[pos_k].astype(np.float64)
            pos_prev = np.roll(pos, 1)
            pos_prev[0] = pos[0]
            score += np.where((pos_prev > 0.8) & (pos < 0.7), 2, 0) * cfg["w_exit_dc_drop"]

    # --- WT BEAR ALIGNMENT ---
    wt_bear_align = d.get("wt_bear_alignment", np.zeros(n, dtype=np.int8)).astype(np.float64)
    score += np.where(wt_bear_align >= 3, 3, 0) * cfg["w_exit_bear_align"]

    # --- WT COMPOSITE FLIP ---
    wt_comp_delta = d.get("wt_composite_delta", np.zeros(n, dtype=np.float32)).astype(np.float64)
    score += np.where(wt_comp_delta < -cfg["exit_composite_threshold"], 2, 0) * cfg["w_exit_composite_flip"]

    exit_signal = score >= cfg["exit_threshold"]
    return exit_signal, score


def build_exit_short(d, cfg):
    """Exit signal for SHORT positions — mirror of exit_long."""
    n = len(d["close"])
    score = np.zeros(n, dtype=np.float64)

    for tf in TFS:
        cb_k = f"wt_cross_bull_{tf}"
        if cb_k in d:
            cb = d[cb_k].astype(np.float64)
            weight = cfg["exit_cross_weights"].get(tf, 1.0)
            score += cb * weight

    for tf in TFS:
        pct_k = f"wt_percentile_{tf}"
        vel_k = f"wt_velocity_{tf}"
        if pct_k in d and vel_k in d:
            pct = d[pct_k].astype(np.float64)
            vel = d[vel_k].astype(np.float64)
            score += np.where((pct < (100 - cfg["exit_overbought_pct"])) & (vel > 0), 2, 0) * cfg["w_exit_ob_turn"]

    for tf in TFS:
        ts_k = f"wt_trough_structure_{tf}"
        if ts_k in d:
            ts = d[ts_k].astype(np.float64)
            score += np.where(ts == 1, 2, 0) * cfg["w_exit_lh"]  # HL = uptrend starting

        ps_k = f"wt_peak_structure_{tf}"
        if ps_k in d:
            ps = d[ps_k].astype(np.float64)
            score += np.where(ps == 1, 3, 0) * cfg["w_exit_ll"]  # HH = breakout

    for tf in TFS:
        div_k = f"wt_divergence_{tf}"
        if div_k in d:
            div = d[div_k].astype(np.float64)
            score += np.where((div == 1) | (div == 3), 3, 0) * cfg["w_exit_bear_div"]

    for tf in TFS:
        ms_k = f"wt_momentum_state_{tf}"
        if ms_k in d:
            ms = d[ms_k].astype(np.float64)
            score += np.where(ms == -2, 2, 0) * cfg["w_exit_exhaust"]  # EXHAUST_DOWN
            score += np.where(ms == 1, 3, 0) * cfg["w_exit_impulse_against"]  # IMPULSE_UP

    for tf in TFS:
        k = f"dc_basis_crossover_{tf}"
        if k in d:
            score += d[k].astype(np.float64) * cfg["w_exit_dc_basis_cross"]

    wt_bull_align = d.get("wt_bull_alignment", np.zeros(n, dtype=np.int8)).astype(np.float64)
    score += np.where(wt_bull_align >= 3, 3, 0) * cfg["w_exit_bear_align"]

    wt_comp_delta = d.get("wt_composite_delta", np.zeros(n, dtype=np.float32)).astype(np.float64)
    score += np.where(wt_comp_delta > cfg["exit_composite_threshold"], 2, 0) * cfg["w_exit_composite_flip"]

    exit_signal = score >= cfg["exit_threshold"]
    return exit_signal, score


# ------------------------------------------------------------------ #
#  PHASE 3: Backtest Engine
# ------------------------------------------------------------------ #
def backtest_symbol(d, cfg, direction="BOTH"):
    """
    Run backtest on a single symbol's NPZ data.
    Returns dict with stats.
    """
    close = d["close"].astype(np.float64)
    n = len(close)

    # Build signals
    entry_long, entry_long_score = build_entry_long(d, cfg)
    entry_short, entry_short_score = build_entry_short(d, cfg)
    exit_long, exit_long_score = build_exit_long(d, cfg)
    exit_short, exit_short_score = build_exit_short(d, cfg)

    # Skip warmup period (first 500 bars for indicators to stabilize)
    warmup = 500

    trades = []
    equity = [1.0]
    position = None  # {"side": "LONG"/"SHORT", "entry_price": float, "entry_bar": int}
    max_hold = cfg.get("max_hold_bars", 384)  # 384 * 15m = 4 days default
    cooldown = 0

    for i in range(warmup, n):
        if cooldown > 0:
            cooldown -= 1

        if position is None:
            # Look for entry
            if cooldown <= 0:
                if direction in ("BOTH", "LONG") and entry_long[i]:
                    position = {"side": "LONG", "entry_price": close[i], "entry_bar": i, "entry_score": entry_long_score[i]}
                elif direction in ("BOTH", "SHORT") and entry_short[i]:
                    position = {"side": "SHORT", "entry_price": close[i], "entry_bar": i, "entry_score": entry_short_score[i]}
            equity.append(equity[-1])
        else:
            # Check exit
            bars_held = i - position["entry_bar"]
            pnl_pct = 0.0

            if position["side"] == "LONG":
                pnl_pct = (close[i] - position["entry_price"]) / position["entry_price"]
                should_exit = exit_long[i] or bars_held >= max_hold
            else:
                pnl_pct = (position["entry_price"] - close[i]) / position["entry_price"]
                should_exit = exit_short[i] or bars_held >= max_hold

            if should_exit:
                # Close trade
                fee = cfg.get("fee_pct", 0.075) / 100 * 2  # round trip
                net_pnl = pnl_pct - fee
                trades.append({
                    "side": position["side"],
                    "entry_price": position["entry_price"],
                    "exit_price": close[i],
                    "entry_bar": position["entry_bar"],
                    "exit_bar": i,
                    "bars_held": bars_held,
                    "pnl_pct": net_pnl,
                    "entry_score": position["entry_score"],
                })
                equity.append(equity[-1] * (1 + net_pnl))
                position = None
                cooldown = cfg.get("cooldown_bars", 4)
            else:
                equity.append(equity[-1] * (1 + (pnl_pct - (equity[-1] - equity[-2 if len(equity) > 1 else -1]) / equity[-2 if len(equity) > 1 else -1] if len(equity) > 1 else 0)))
                # Simpler: just track equity based on current unrealized
                equity[-1] = equity[-2] * (1 + pnl_pct) if len(equity) > 1 else 1 + pnl_pct

    # Close any open position at end
    if position is not None:
        pnl_pct = 0.0
        if position["side"] == "LONG":
            pnl_pct = (close[-1] - position["entry_price"]) / position["entry_price"]
        else:
            pnl_pct = (position["entry_price"] - close[-1]) / position["entry_price"]
        fee = cfg.get("fee_pct", 0.075) / 100 * 2
        net_pnl = pnl_pct - fee
        trades.append({
            "side": position["side"],
            "entry_price": position["entry_price"],
            "exit_price": close[-1],
            "entry_bar": position["entry_bar"],
            "exit_bar": n - 1,
            "bars_held": n - 1 - position["entry_bar"],
            "pnl_pct": net_pnl,
            "entry_score": position["entry_score"],
        })
        equity.append(equity[-1] * (1 + net_pnl))

    # Compute stats
    equity = np.array(equity)
    if len(trades) == 0:
        return {"n_trades": 0, "sharpe": 0, "total_return": 0, "win_rate": 0, "pf": 0, "max_dd": 0, "avg_hold": 0}

    pnls = np.array([t["pnl_pct"] for t in trades])
    returns = np.diff(equity) / np.where(equity[:-1] > 0, equity[:-1], 1)
    returns = returns[np.isfinite(returns)]

    winners = pnls[pnls > 0]
    losers = pnls[pnls < 0]

    stats = {
        "n_trades": len(trades),
        "sharpe": safe_sharpe(returns),
        "total_return": float(equity[-1] / equity[0] - 1) * 100,
        "win_rate": float(len(winners) / len(pnls)) * 100 if len(pnls) > 0 else 0,
        "pf": profit_factor(pnls),
        "max_dd": max_drawdown(equity) * 100,
        "avg_pnl": float(np.mean(pnls)) * 100,
        "avg_winner": float(np.mean(winners)) * 100 if len(winners) > 0 else 0,
        "avg_loser": float(np.mean(losers)) * 100 if len(losers) > 0 else 0,
        "avg_hold": float(np.mean([t["bars_held"] for t in trades])),
        "long_trades": sum(1 for t in trades if t["side"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["side"] == "SHORT"),
        "trades": trades,
        "equity": equity,
    }
    return stats


def run_full_backtest(npz_dir, cfg, direction="BOTH"):
    """Run backtest across ALL symbols in npz_dir."""
    files = sorted([f for f in os.listdir(npz_dir) if f.endswith(".npz")])

    print(f"\n{'='*80}")
    print(f"PHASE 3: Backtesting {len(files)} symbols | Direction: {direction}")
    print(f"{'='*80}\n")

    all_stats = {}
    all_trades = []
    all_equity = []
    total_bars = 0

    for fi, fname in enumerate(files):
        sym = fname.replace(".npz", "")
        d = np.load(os.path.join(npz_dir, fname), allow_pickle=True)
        stats = backtest_symbol(d, cfg, direction)
        all_stats[sym] = {k: v for k, v in stats.items() if k not in ("trades", "equity")}

        if stats["n_trades"] > 0:
            all_trades.extend(stats["trades"])
            total_bars = max(total_bars, len(d["close"]))

        print(f"  {sym:<16} trades={stats['n_trades']:>5}  Sharpe={stats['sharpe']:>8.2f}  "
              f"Return={stats['total_return']:>8.1f}%  WR={stats['win_rate']:>5.1f}%  "
              f"PF={stats['pf']:>6.2f}  MaxDD={stats['max_dd']:>7.2f}%")

    # Aggregate stats
    if not all_trades:
        print("\nNO TRADES GENERATED. Check entry thresholds.")
        return all_stats, {}

    all_pnls = np.array([t["pnl_pct"] for t in all_trades])
    winners = all_pnls[all_pnls > 0]
    losers = all_pnls[all_pnls < 0]

    # Build combined equity curve (sequential trades sorted by entry bar)
    all_trades.sort(key=lambda t: t["entry_bar"])
    combined_equity = [1.0]
    for t in all_trades:
        combined_equity.append(combined_equity[-1] * (1 + t["pnl_pct"]))
    combined_equity = np.array(combined_equity)
    combined_returns = np.diff(combined_equity) / np.where(combined_equity[:-1] > 0, combined_equity[:-1], 1)

    agg = {
        "total_trades": len(all_trades),
        "total_symbols": len([s for s, st in all_stats.items() if st["n_trades"] > 0]),
        "sharpe": safe_sharpe(combined_returns, periods_per_year=len(combined_returns)),
        "total_return": float(combined_equity[-1] / combined_equity[0] - 1) * 100,
        "win_rate": float(len(winners) / len(all_pnls)) * 100,
        "pf": profit_factor(all_pnls),
        "max_dd": max_drawdown(combined_equity) * 100,
        "avg_pnl": float(np.mean(all_pnls)) * 100,
        "avg_winner": float(np.mean(winners)) * 100 if len(winners) > 0 else 0,
        "avg_loser": float(np.mean(losers)) * 100 if len(losers) > 0 else 0,
        "avg_hold_bars": float(np.mean([t["bars_held"] for t in all_trades])),
        "long_trades": sum(1 for t in all_trades if t["side"] == "LONG"),
        "short_trades": sum(1 for t in all_trades if t["side"] == "SHORT"),
        "best_symbol": max(all_stats.items(), key=lambda x: x[1].get("total_return", 0))[0] if all_stats else "",
        "worst_symbol": min(all_stats.items(), key=lambda x: x[1].get("total_return", 0))[0] if all_stats else "",
    }

    print(f"\n{'='*80}")
    print("AGGREGATE RESULTS")
    print(f"{'='*80}")
    print(f"  Total trades:     {agg['total_trades']}")
    print(f"  Active symbols:   {agg['total_symbols']}/{len(files)}")
    print(f"  Sharpe ratio:     {agg['sharpe']:.2f}")
    print(f"  Total return:     {agg['total_return']:.1f}%")
    print(f"  Win rate:         {agg['win_rate']:.1f}%")
    print(f"  Profit factor:    {agg['pf']:.2f}")
    print(f"  Max drawdown:     {agg['max_dd']:.2f}%")
    print(f"  Avg PnL/trade:    {agg['avg_pnl']:.3f}%")
    print(f"  Avg winner:       {agg['avg_winner']:.3f}%")
    print(f"  Avg loser:        {agg['avg_loser']:.3f}%")
    print(f"  Avg hold (bars):  {agg['avg_hold_bars']:.1f}")
    print(f"  Long trades:      {agg['long_trades']}")
    print(f"  Short trades:     {agg['short_trades']}")
    print(f"  Best symbol:      {agg['best_symbol']}")
    print(f"  Worst symbol:     {agg['worst_symbol']}")

    return all_stats, agg


# ------------------------------------------------------------------ #
#  CONFIG DEFINITIONS
# ------------------------------------------------------------------ #
def get_base_config():
    """Base config — balanced starting point."""
    return {
        # Entry weights
        "w_bull_alignment": 3.0,
        "w_composite_long": 5.0,
        "w_composite_delta": 4.0,
        "w_velocity_pos": 0.5,
        "w_accel_pos": 0.3,
        "w_oversold": 1.5,
        "w_zscore_deep": 1.0,
        "w_bullish_tf": 0.8,
        "w_hl_structure": 2.0,
        "w_hh_structure": 1.0,
        "w_bull_div": 2.5,
        "w_div_strength": 1.0,
        "w_dc_low": 1.5,
        "w_dc_breakout": 0.5,
        "w_dc_expansion": 0.5,
        "w_rising_crosses": 2.0,
        "w_hl_count": 2.0,
        "w_hh_count": 1.5,
        "w_oversold_tfs": 1.5,
        "w_impulse_up": 1.5,
        "w_exhaust_down": 1.0,
        "w_wave_phase_bull": 0.5,
        "w_wt_score": 1.0,
        "w_deep_cross": 2.0,
        "w_neg_cross": 0.5,
        "w_rising_bull_cross": 1.5,
        "w_trough_hl": 1.5,
        # Entry thresholds
        "oversold_pct": 25.0,
        "dc_low_threshold": 0.25,
        "dc_high_threshold": 0.85,
        "entry_threshold": 15.0,
        # Exit weights
        "exit_cross_weights": {"3m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 4.0, "D": 5.0},
        "w_exit_ob_turn": 1.5,
        "w_exit_lh": 1.5,
        "w_exit_ll": 2.0,
        "w_exit_bear_div": 2.0,
        "w_exit_exhaust": 1.5,
        "w_exit_impulse_against": 2.5,
        "w_exit_dc_basis_cross": 1.5,
        "w_exit_dc_drop": 1.0,
        "w_exit_bear_align": 2.0,
        "w_exit_composite_flip": 1.5,
        "exit_overbought_pct": 75.0,
        "exit_composite_threshold": 30.0,
        "exit_threshold": 8.0,
        # Trade management
        "max_hold_bars": 384,  # 4 days at 15m
        "cooldown_bars": 4,
        "fee_pct": 0.075,
    }


def get_sweep_configs():
    """Generate config variants for sweep."""
    base = get_base_config()
    configs = {"BASE": base}

    # Variant 1: Aggressive entries (lower threshold)
    v1 = base.copy()
    v1["entry_threshold"] = 10.0
    v1["exit_threshold"] = 10.0
    configs["AGGRESSIVE"] = v1

    # Variant 2: Conservative entries (higher threshold)
    v2 = base.copy()
    v2["entry_threshold"] = 25.0
    v2["exit_threshold"] = 6.0
    configs["CONSERVATIVE"] = v2

    # Variant 3: Structure-heavy (weight WT structure signals more)
    v3 = base.copy()
    v3["w_hl_structure"] = 4.0
    v3["w_hh_structure"] = 3.0
    v3["w_bull_div"] = 4.0
    v3["w_exit_lh"] = 3.0
    v3["w_exit_ll"] = 4.0
    v3["w_exit_bear_div"] = 4.0
    configs["STRUCTURE_HEAVY"] = v3

    # Variant 4: Momentum-heavy (weight velocity/acceleration more)
    v4 = base.copy()
    v4["w_velocity_pos"] = 2.0
    v4["w_accel_pos"] = 1.5
    v4["w_impulse_up"] = 3.0
    v4["w_exit_exhaust"] = 3.0
    v4["w_exit_impulse_against"] = 4.0
    configs["MOMENTUM_HEAVY"] = v4

    # Variant 5: DC-heavy (weight Donchian signals more)
    v5 = base.copy()
    v5["w_dc_low"] = 4.0
    v5["w_dc_breakout"] = 3.0
    v5["w_dc_expansion"] = 2.0
    v5["w_exit_dc_basis_cross"] = 3.0
    v5["w_exit_dc_drop"] = 3.0
    configs["DC_HEAVY"] = v5

    # Variant 6: Oversold mean-reversion focus
    v6 = base.copy()
    v6["w_oversold"] = 4.0
    v6["w_zscore_deep"] = 3.0
    v6["w_oversold_tfs"] = 3.0
    v6["oversold_pct"] = 20.0
    v6["w_exit_ob_turn"] = 3.0
    v6["exit_overbought_pct"] = 70.0
    configs["MEAN_REVERSION"] = v6

    # Variant 7: Cross-quality focus (deep + rising crosses)
    v7 = base.copy()
    v7["w_deep_cross"] = 5.0
    v7["w_neg_cross"] = 2.0
    v7["w_rising_bull_cross"] = 4.0
    v7["entry_threshold"] = 12.0
    configs["CROSS_QUALITY"] = v7

    # Variant 8: Quick trades (shorter hold, tighter exit)
    v8 = base.copy()
    v8["max_hold_bars"] = 96  # 1 day
    v8["exit_threshold"] = 5.0
    v8["entry_threshold"] = 12.0
    configs["QUICK_TRADES"] = v8

    # Variant 9: Swing trades (longer hold, relaxed exit)
    v9 = base.copy()
    v9["max_hold_bars"] = 960  # 10 days
    v9["exit_threshold"] = 12.0
    v9["entry_threshold"] = 20.0
    configs["SWING_TRADES"] = v9

    # Variant 10: HTF alignment focus
    v10 = base.copy()
    v10["w_bull_alignment"] = 6.0
    v10["w_composite_long"] = 8.0
    v10["w_composite_delta"] = 6.0
    v10["w_exit_bear_align"] = 4.0
    v10["w_exit_composite_flip"] = 3.0
    v10["entry_threshold"] = 18.0
    configs["HTF_ALIGNMENT"] = v10

    # Variant 11: Wave phase + divergence combo
    v11 = base.copy()
    v11["w_wave_phase_bull"] = 3.0
    v11["w_bull_div"] = 5.0
    v11["w_div_strength"] = 3.0
    v11["w_exit_bear_div"] = 5.0
    configs["WAVE_DIV"] = v11

    # Variant 12: All weights doubled (more selective threshold)
    v12 = {k: v * 2 if isinstance(v, (int, float)) and k.startswith("w_") else v for k, v in base.items()}
    v12["entry_threshold"] = 35.0
    v12["exit_threshold"] = 18.0
    configs["DOUBLE_WEIGHT"] = v12

    return configs


# ------------------------------------------------------------------ #
#  OPTIMIZER: Iterate on best config
# ------------------------------------------------------------------ #
def optimize_config(npz_dir, base_cfg, iterations=5):
    """Simple hill-climbing optimizer on config weights."""
    print(f"\n{'='*80}")
    print(f"OPTIMIZER: {iterations} iterations of hill-climbing")
    print(f"{'='*80}\n")

    best_cfg = base_cfg.copy()
    _, best_agg = run_full_backtest(npz_dir, best_cfg)
    best_sharpe = best_agg.get("sharpe", 0)
    print(f"\n  Starting Sharpe: {best_sharpe:.2f}")

    tunable_keys = [k for k in best_cfg if isinstance(best_cfg[k], (int, float)) and k != "fee_pct"]

    for iteration in range(iterations):
        print(f"\n--- Optimization iteration {iteration+1}/{iterations} ---")
        improved = False

        for key in tunable_keys:
            original = best_cfg[key]
            # Try +20% and -20%
            for delta in [0.2, -0.2, 0.5, -0.5]:
                trial_cfg = best_cfg.copy()
                trial_cfg[key] = original * (1 + delta)
                if trial_cfg[key] < 0 and key not in ("w_wt_score",):
                    continue

                _, trial_agg = run_full_backtest(npz_dir, trial_cfg, direction="BOTH")
                trial_sharpe = trial_agg.get("sharpe", 0)

                # Also require reasonable trade count
                if trial_agg.get("total_trades", 0) < 50:
                    continue

                if trial_sharpe > best_sharpe:
                    best_sharpe = trial_sharpe
                    best_cfg = trial_cfg
                    improved = True
                    print(f"  IMPROVED: {key} {original:.3f} -> {trial_cfg[key]:.3f} | Sharpe: {best_sharpe:.2f}")
                    break

        if not improved:
            print(f"  No improvement in iteration {iteration+1}, stopping.")
            break

    return best_cfg, best_sharpe


# ------------------------------------------------------------------ #
#  MAIN
# ------------------------------------------------------------------ #
def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Determine which NPZ dir to use
    npz_dir = NPZ_DIR
    if not os.path.isdir(npz_dir):
        print(f"NPZ dir not found: {npz_dir}")
        sys.exit(1)

    files = [f for f in os.listdir(npz_dir) if f.endswith(".npz")]
    print(f"Found {len(files)} NPZ files in {npz_dir}")

    mode = sys.argv[1] if len(sys.argv) > 1 else "full"

    if mode == "analyze":
        # Phase 1 only
        results = analyze_metric_power(npz_dir)
        with open(os.path.join(RESULTS_DIR, "metric_analysis.json"), "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nResults saved to {RESULTS_DIR}/metric_analysis.json")

    elif mode == "sweep":
        # Phase 3: Sweep all config variants
        configs = get_sweep_configs()
        sweep_results = {}

        for name, cfg in configs.items():
            print(f"\n{'#'*80}")
            print(f"CONFIG: {name}")
            print(f"{'#'*80}")
            per_sym, agg = run_full_backtest(npz_dir, cfg)
            sweep_results[name] = {"config": {k: v for k, v in cfg.items() if not isinstance(v, dict)}, "aggregate": agg, "per_symbol": per_sym}

        # Rank by Sharpe
        print(f"\n{'='*80}")
        print("SWEEP RANKING (by Sharpe)")
        print(f"{'='*80}")
        ranked = sorted(sweep_results.items(), key=lambda x: x[1]["aggregate"].get("sharpe", 0), reverse=True)
        print(f"\n{'Config':<20} {'Sharpe':>8} {'Return':>10} {'Trades':>8} {'WR':>7} {'PF':>7} {'MaxDD':>8}")
        print("-" * 75)
        for name, data in ranked:
            a = data["aggregate"]
            print(f"{name:<20} {a.get('sharpe', 0):>8.2f} {a.get('total_return', 0):>9.1f}% {a.get('total_trades', 0):>8} {a.get('win_rate', 0):>6.1f}% {a.get('pf', 0):>7.2f} {a.get('max_dd', 0):>7.2f}%")

        with open(os.path.join(RESULTS_DIR, "sweep_results.json"), "w") as f:
            json.dump({k: {"config": v["config"], "aggregate": v["aggregate"]} for k, v in sweep_results.items()}, f, indent=2, default=str)

        # Return best config name for optimization
        if ranked:
            best_name = ranked[0][0]
            print(f"\nBest config: {best_name} (Sharpe: {ranked[0][1]['aggregate'].get('sharpe', 0):.2f})")
            return best_name

    elif mode == "optimize":
        # Phase 3b: Optimize best config
        configs = get_sweep_configs()
        # Start from best sweep result if available
        start_cfg_name = sys.argv[2] if len(sys.argv) > 2 else "BASE"
        start_cfg = configs.get(start_cfg_name, get_base_config())
        print(f"Starting optimization from config: {start_cfg_name}")
        best_cfg, best_sharpe = optimize_config(npz_dir, start_cfg, iterations=3)
        print(f"\nFinal optimized Sharpe: {best_sharpe:.2f}")
        with open(os.path.join(RESULTS_DIR, "optimized_config.json"), "w") as f:
            json.dump({"sharpe": best_sharpe, "config": {k: v for k, v in best_cfg.items() if not isinstance(v, dict)}}, f, indent=2, default=str)

    else:
        # Full pipeline: analyze -> sweep -> report
        print("=" * 80)
        print("WT/DC RESEARCH ENGINE — FULL PIPELINE")
        print(f"Symbols: {len(files)} | Fields per symbol: ~337 | Timeframes: {TFS}")
        print("=" * 80)

        # Phase 1
        analysis = analyze_metric_power(npz_dir)

        # Phase 3: Sweep
        configs = get_sweep_configs()
        sweep_results = {}
        for name, cfg in configs.items():
            print(f"\n{'#'*80}")
            print(f"CONFIG: {name}")
            print(f"{'#'*80}")
            per_sym, agg = run_full_backtest(npz_dir, cfg)
            sweep_results[name] = {"aggregate": agg, "per_symbol": per_sym}

        # Final ranking
        print(f"\n{'='*80}")
        print("FINAL SWEEP RANKING")
        print(f"{'='*80}")
        ranked = sorted(sweep_results.items(), key=lambda x: x[1]["aggregate"].get("sharpe", 0), reverse=True)
        print(f"\n{'Config':<20} {'Sharpe':>8} {'Return':>10} {'Trades':>8} {'WR':>7} {'PF':>7} {'MaxDD':>8}")
        print("-" * 75)
        for name, data in ranked:
            a = data["aggregate"]
            print(f"{name:<20} {a.get('sharpe', 0):>8.2f} {a.get('total_return', 0):>9.1f}% {a.get('total_trades', 0):>8} {a.get('win_rate', 0):>6.1f}% {a.get('pf', 0):>7.2f} {a.get('max_dd', 0):>7.2f}%")

        # Save everything
        with open(os.path.join(RESULTS_DIR, "full_results.json"), "w") as f:
            json.dump({
                "timestamp": datetime.utcnow().isoformat(),
                "n_symbols": len(files),
                "analysis_top_metrics": analysis.get("metric_scores", [])[:20],
                "sweep_ranking": [{
                    "config": name,
                    "sharpe": data["aggregate"].get("sharpe", 0),
                    "total_return": data["aggregate"].get("total_return", 0),
                    "total_trades": data["aggregate"].get("total_trades", 0),
                    "win_rate": data["aggregate"].get("win_rate", 0),
                    "pf": data["aggregate"].get("pf", 0),
                    "max_dd": data["aggregate"].get("max_dd", 0),
                } for name, data in ranked],
            }, f, indent=2, default=str)

        print(f"\nResults saved to {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
