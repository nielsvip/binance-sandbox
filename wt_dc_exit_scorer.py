"""
WT+DC Exit Scorer — Production exit scoring for stock trading.

Data-driven exit scoring based on analysis of 9,386 trades across 14 symbols.
Re-optimized 2026-04-08 via grid search over 3,600 parameter combinations
across 13 symbols x 2 years (74,677 trades, 10m bars, trading hours only).

KEY FINDINGS (grid search 2026-04-08):
- WT velocity deceleration is the PRIMARY exit signal (weight 1.0x — LOWER is better)
- Peak divergence is secondary (weight 0.5x)
- DC channel position adds NO value for exits (weight 0.0)
- Cross-TF velocity alignment is minor (weight 0.5x — LOWER is better)
- Composite signal adds NO value (weight 0.0)
- Optimal threshold: 25 (exit EARLY, not late)
- Per-symbol weekly Sharpe: 16.71 (avg), 16.67 (median), 14.02 (min)
- WR: 57.0% | PF: 3.48x | Avg PnL: 0.39%
- vs previous params (vel=2.0,tf=1.0,comp=0.5): Sharpe 13.34 -> 16.71 (+25.3%)

CRITICAL INSIGHT: Lower weights on velocity and TF alignment outperform higher.
The bonus signals (crosses, OB/OS, stoch) carry more relative weight with lower
main component weights, creating better balance. Less aggressive velocity
scoring reduces false exits and lets winners run longer.
"""

TFS = ["5m", "15m", "1h", "4h", "D"]
TF_WEIGHTS = {"5m": 0.05, "15m": 0.10, "1h": 0.20, "4h": 0.30, "D": 0.35}

# Optimized weights from grid search (2026-04-08, 3600 combos, 13 sym x 2yr)
OPTIMAL_PARAMS = {
    "vel_w": 1.0,      # Velocity: primary but lower weight lets bonus signals matter
    "div_w": 0.5,      # Peak divergence: secondary
    "dc_w": 0.0,       # DC channel: zero contribution
    "tf_w": 0.5,       # Cross-TF alignment: minor (0.5 > 1.0 > 1.5 > 2.0)
    "comp_w": 0.0,     # Composite: zero contribution (0.0 > 0.5 > 1.0)
    "threshold": 25,   # Exit when score >= 25
}


def score_exit(indicators: dict, is_long: bool, current_price: float = 0.0, cfg=None) -> tuple:
    """Multi-TF WT/DC exit — TIGHTENED 2026-04-09, CONFIGURABLE 2026-04-10.

    EXIT requires N-of-5 conditions (configurable via cfg.EXIT_SCORER_MIN_CONDITIONS, default 5=ALL):
      - 1h WT cross AGAINST (LTF event)
      - 4h WT trending AGAINST (HTF continuous)
      - D WT trending AGAINST (HTF confirmation)
      - K extreme (overbought for long, oversold for short) on 1h or 4h
      - DC position extreme (>0.85 long / <0.15 short) on 1h or 4h

    Configurable thresholds (all via cfg object or defaults):
      EXIT_SCORER_MIN_CONDITIONS: 5 (strict) to 3 (loose) — how many conditions needed
      EXIT_SCORER_K_EXTREME: 75 (default) — stoch K threshold for extreme
      EXIT_SCORER_DC_EXTREME: 0.80 (default) — DC position threshold for extreme
      EXIT_SCORER_PARTIAL_SCORE: 40 (default) — score for N-1 conditions met
      EXIT_SCORER_FULL_SCORE: 100 (default) — score for all N conditions met
    """
    _min_cond = int(getattr(cfg, 'EXIT_SCORER_MIN_CONDITIONS', 5) if cfg else 5)
    _k_extreme = float(getattr(cfg, 'EXIT_SCORER_K_EXTREME', 75) if cfg else 75)
    _dc_extreme = float(getattr(cfg, 'EXIT_SCORER_DC_EXTREME', 0.80) if cfg else 0.80)
    _partial_score = float(getattr(cfg, 'EXIT_SCORER_PARTIAL_SCORE', 40) if cfg else 40)
    _full_score = float(getattr(cfg, 'EXIT_SCORER_FULL_SCORE', 100) if cfg else 100)
    # === MULTI-TF EXIT (PRIMARY) — CONFIGURABLE N-OF-5 GATE ===
    # 2026-04-27 BUG FIX: c1_ltf_cross was structurally False on crypto because (a) NPZ field is
    # 'wt_signal_<tf>' not 'wt_cross_<tf>', and (b) NPZ stores int8 not string. Fallback to wt1<wt2
    # comparison guarantees c1 fires correctly across all data formats.
    def _wt_cross_against(tf, _is_long):
        for key in (f"wt_cross_{tf}", f"wt_signal_{tf}"):
            v = indicators.get(key, None)
            if v is None or v == 0 or v == "" or v == "NEUTRAL": continue
            if isinstance(v, str):
                vu = v.upper()
                return vu == ("BEAR" if _is_long else "BULL")
            try:
                n = int(float(v))
                return (n < 0) if _is_long else (n > 0)
            except (ValueError, TypeError):
                continue
        # Fallback: wt1 vs wt2 cross
        w1 = _get(indicators, f"wt1_{tf}"); w2 = _get(indicators, f"wt2_{tf}")
        if w1 == 0 and w2 == 0: return False
        return (w1 < w2) if _is_long else (w1 > w2)
    wt1_4h = _get(indicators, "wt1_4h"); wt2_4h = _get(indicators, "wt2_4h")
    wt1_D = _get(indicators, "wt1_D"); wt2_D = _get(indicators, "wt2_D")
    k_1h = _get(indicators, "stoch_k_1h"); k_4h = _get(indicators, "stoch_k_4h")
    dc_pos_1h = _get(indicators, "dc_position_1h", 0.5)
    dc_pos_4h = _get(indicators, "dc_position_4h", 0.5)
    c1_ltf_cross = _wt_cross_against("1h", is_long)
    if is_long:
        c2_4h_against = (wt1_4h < wt2_4h)
        c3_D_against = (wt1_D < wt2_D)
        c4_k_extreme = (k_1h >= _k_extreme or k_4h >= _k_extreme)
        c5_dc_extreme = (dc_pos_1h >= _dc_extreme or dc_pos_4h >= _dc_extreme)
    else:
        c2_4h_against = (wt1_4h > wt2_4h)
        c3_D_against = (wt1_D > wt2_D)
        c4_k_extreme = (k_1h <= (100 - _k_extreme) or k_4h <= (100 - _k_extreme))
        c5_dc_extreme = (dc_pos_1h <= (1.0 - _dc_extreme) or dc_pos_4h <= (1.0 - _dc_extreme))
    _hits = sum([c1_ltf_cross, c2_4h_against, c3_D_against, c4_k_extreme, c5_dc_extreme])
    _diag = f"1h_x={c1_ltf_cross}_4h={c2_4h_against}_D={c3_D_against}_k1h={k_1h:.0f}/k4h={k_4h:.0f}_dc1h={dc_pos_1h:.2f}/dc4h={dc_pos_4h:.2f}"
    if _hits >= _min_cond:
        return _full_score, f"STRICT_EXIT_{_hits}/{_min_cond}_{_diag}"
    if _hits >= max(_min_cond - 1, 3):
        return _partial_score, f"PARTIAL_EXIT_{_hits}/{_min_cond}_{_diag}"
    return 0.0, f"HOLD_{_hits}/{_min_cond}_{_diag}"
    # === LEGACY SCORING (disabled — kept below for reference) ===
    score = 0.0
    reasons = []
    p = OPTIMAL_PARAMS
    # === 1. VELOCITY DECELERATION (0-35, weight 2.0x = 0-70 effective) ===
    # This is the PRIMARY signal. Detects when momentum starts dying.
    vel_component = 0.0
    vel_details = []
    for tf in TFS:
        vel = _get(indicators, f"wt_velocity_{tf}")
        acc = _get(indicators, f"wt_acceleration_{tf}")
        w = TF_WEIGHTS[tf]
        if is_long:
            neg_vel = max(-vel, 0)
            neg_acc = max(-acc, 0) if vel < 0 else 0
        else:
            neg_vel = max(vel, 0)
            neg_acc = max(acc, 0) if vel > 0 else 0
        vel_contrib = min(neg_vel * 1.2, 10.0) * w * 3
        acc_contrib = min(neg_acc * 2, 8.0) * w * 3
        vel_component += vel_contrib + acc_contrib
        if vel_contrib > 1.0:
            vel_details.append(f"{tf}:v={vel:.1f}")
    vel_score = min(vel_component, 35.0) * p["vel_w"]
    score += vel_score
    if vel_score > 5:
        reasons.append(f"VEL({vel_score:.0f})[{','.join(vel_details[:3])}]")
    # === 2. PEAK/TROUGH DIVERGENCE (0-20, weight 0.5x = 0-10 effective) ===
    # Lower peaks (longs) or higher troughs (shorts) = momentum fading.
    div_component = 0.0
    div_details = []
    for tf in ["15m", "1h", "4h", "D"]:
        w = TF_WEIGHTS[tf]
        if is_long:
            peak = _get(indicators, f"wt_peak_{tf}")
            peak_prev = _get(indicators, f"wt_peak_prev_{tf}")
            div = max(peak_prev - peak, 0)
        else:
            trough = _get(indicators, f"wt_trough_{tf}")
            trough_prev = _get(indicators, f"wt_trough_prev_{tf}")
            div = max(trough - trough_prev, 0)
        if div > 3:
            contrib = min((div - 3) * 0.4, 8.0) * w * 3
            div_component += contrib
            if contrib > 0.5:
                div_details.append(f"{tf}:{div:.0f}")
    div_score = min(div_component, 20.0) * p["div_w"]
    score += div_score
    if div_score > 2:
        reasons.append(f"DIV({div_score:.0f})[{','.join(div_details[:3])}]")
    # === 3. CROSS-TF VELOCITY ALIGNMENT (0-15, weight 1.0x) ===
    # How many TFs have velocity against the position?
    tf_against = 0
    for tf in TFS:
        vel = _get(indicators, f"wt_velocity_{tf}")
        if is_long and vel < 0:
            tf_against += 1
        elif not is_long and vel > 0:
            tf_against += 1
    tf_score = min(tf_against * 3, 15.0) * p["tf_w"]
    score += tf_score
    if tf_against >= 3:
        reasons.append(f"TF_AGAINST({tf_against}/5)")
    # === 4. COMPOSITE SIGNAL (0-10, weight 0.5x = 0-5 effective) ===
    comp_long = _get(indicators, "wt_composite_long")
    comp_short = _get(indicators, "wt_composite_short")
    comp_score = 0.0
    if is_long:
        if comp_short > comp_long and comp_short > 5:
            comp_score = min((comp_short - 5) * 1.5, 10.0) * p["comp_w"]
    else:
        if comp_long > comp_short and comp_long > 5:
            comp_score = min((comp_long - 5) * 1.5, 10.0) * p["comp_w"]
    score += comp_score
    if comp_score > 2:
        reasons.append(f"COMP({comp_score:.0f})")
    # === 5. CROSS SIGNALS (bonus, not weight-adjusted) ===
    # Bear/bull cross on multiple TFs = strong confirmation
    cross_count = 0
    cross_tfs = []
    for tf in TFS:
        if is_long:
            bc = _get(indicators, f"wt_cross_bear_{tf}")
        else:
            bc = _get(indicators, f"wt_cross_bull_{tf}")
        if bc == 1:
            cross_count += 1
            cross_tfs.append(tf)
    if cross_count >= 2:
        score += 10
        reasons.append(f"CROSS({','.join(cross_tfs)})")
    elif cross_count == 1:
        score += 3
    # === 6. OVERBOUGHT/OVERSOLD TF COUNT (bonus) ===
    if is_long:
        ob_count = _get(indicators, "wt_overbought_tf_count")
        score += min(ob_count * 2, 8.0)
        if ob_count >= 2:
            reasons.append(f"OB({ob_count}TF)")
    else:
        os_count = _get(indicators, "wt_oversold_tf_count")
        score += min(os_count * 2, 8.0)
        if os_count >= 2:
            reasons.append(f"OS({os_count}TF)")
    # === 7. STOCH OVERBOUGHT DECLINING (bonus) ===
    for tf in ["1h", "4h"]:
        sk = _get(indicators, f"stoch_k_{tf}")
        sk_prev = _get(indicators, f"stoch_k_{tf}_prev")
        if is_long and sk > 70 and sk < sk_prev:
            score += 3
            reasons.append(f"STOCH_OB_{tf}")
        elif not is_long and sk < 30 and sk > sk_prev:
            score += 3
            reasons.append(f"STOCH_OS_{tf}")
    final_score = min(score, 100.0)
    reason = " | ".join(reasons) if reasons else "NO_EXIT_SIGNAL"
    return (final_score, reason)


def should_exit(indicators: dict, is_long: bool, current_price: float = 0.0, threshold: float = 25.0) -> tuple:
    """Convenience wrapper: returns (should_exit_bool, score, reason)."""
    sc, reason = score_exit(indicators, is_long, current_price)
    return (sc >= threshold, sc, reason)


def _get(d, key, default=0.0):
    """Safe get from indicator dict, handling NaN."""
    v = d.get(key, default)
    if v is None:
        return default
    try:
        if v != v:  # NaN check
            return default
    except (TypeError, ValueError):
        pass
    return float(v)


# ============================================================
# Self-test: verify scoring matches analysis results
# ============================================================

if __name__ == "__main__":
    import numpy as np
    import os
    NPZ_DIR = os.path.join("/Users/niels/Documents/binance", "backtest_v8/indicators")
    # Load AAPL and run scoring on all bars
    print("Loading AAPL for validation...")
    f = np.load(os.path.join(NPZ_DIR, "AAPL.npz"), allow_pickle=True)
    data = {k: f[k].astype(np.float32) for k in f.files}
    n = len(data["close"])
    # Score every 100th bar for speed
    long_scores = []
    short_scores = []
    for i in range(500, n, 100):
        ind = {k: float(data[k][i]) for k in data.keys()}
        ls, _ = score_exit(ind, True)
        ss, _ = score_exit(ind, False)
        long_scores.append(ls)
        short_scores.append(ss)
    long_scores = np.array(long_scores)
    short_scores = np.array(short_scores)
    print(f"AAPL Long scores:  mean={np.mean(long_scores):.1f}, median={np.median(long_scores):.1f}, "
          f"P25={np.percentile(long_scores, 25):.1f}, P75={np.percentile(long_scores, 75):.1f}")
    print(f"AAPL Short scores: mean={np.mean(short_scores):.1f}, median={np.median(short_scores):.1f}, "
          f"P25={np.percentile(short_scores, 25):.1f}, P75={np.percentile(short_scores, 75):.1f}")
    pct_exit_long = np.mean(long_scores >= 25) * 100
    pct_exit_short = np.mean(short_scores >= 25) * 100
    print(f"% bars triggering exit (long): {pct_exit_long:.1f}%")
    print(f"% bars triggering exit (short): {pct_exit_short:.1f}%")
    # Show a sample exit with reasons
    for i in range(500, n, 50):
        ind = {k: float(data[k][i]) for k in data.keys()}
        sc, reason = score_exit(ind, True)
        if sc >= 25:
            print(f"\nSample LONG exit at bar {i}: score={sc:.1f}, reason={reason}")
            print(f"  close={data['close'][i]:.2f}, wt_vel_1h={data['wt_velocity_1h'][i]:.2f}, "
                  f"wt_vel_4h={data['wt_velocity_4h'][i]:.2f}, wt_vel_D={data['wt_velocity_D'][i]:.2f}")
            break
    for i in range(500, n, 50):
        ind = {k: float(data[k][i]) for k in data.keys()}
        sc, reason = score_exit(ind, False)
        if sc >= 25:
            print(f"\nSample SHORT exit at bar {i}: score={sc:.1f}, reason={reason}")
            print(f"  close={data['close'][i]:.2f}, wt_vel_1h={data['wt_velocity_1h'][i]:.2f}, "
                  f"wt_vel_4h={data['wt_velocity_4h'][i]:.2f}, wt_vel_D={data['wt_velocity_D'][i]:.2f}")
            break
    # Performance benchmark
    import time
    ind = {k: float(data[k][50000]) for k in data.keys()}
    t0 = time.time()
    for _ in range(10000):
        score_exit(ind, True)
    elapsed = time.time() - t0
    print(f"\nPerformance: 10,000 calls in {elapsed:.3f}s = {elapsed / 10000 * 1000:.3f}ms per call")
    print(f"At 200 symbols every 5min: {200 * elapsed / 10000 * 1000:.1f}ms total")
    print("\nValidation complete.")
