#!/usr/bin/env python3
"""
v8_reentry_micro_ablation.py — Test every evaluate_reentry block individually.

Each block is a vectorized numpy function that mirrors the real code.
Tests: baseline (no reentry) → add one block → measure ΔSharpe.
Blocks that improve Sharpe = KEEP, blocks that don't = DITCH.

Blocks map to evaluate_reentry lines in ez_manage.py:16160-17337:
  B1: WT_2of3     (16180-16177)  — WT 2/3 TFs in favor → immediate reentry
  B2: BC156_BOTTOM (16178-16209)  — WT15m bounce from oversold + 1h trend
  B3: BC156_CROSS  (16210-16219)  — Exit price crossed + 3/4 WT confirm
  B4: DC_RETEST    (16221-16231)  — DC high break retest at pullback
  B5: TIER2_CHASE  (16375-16397)  — Trend continued past exit, chase at 80%
  B6: SIMPLE       (16460-16509)  — Basic level-cross + WT confirmation
  B7: AGGR_DC      (16560-16601)  — Aggressive DC breakout reentry
  B8: S0_IMMEDIATE (16620-16661)  — Fast post-exit reentry
  B9: S3_SNAPBACK  (16836-16858)  — Momentum snapback at level
  B10: S4_STOCH_REV (16859-16887)  — Stochastic cross reversal
  B11: S5_DC_BREAK  (16888-16927)  — DC channel breakout reentry
  B12: S6_WT_MOM    (16928-16967)  — WT momentum confluence
  B13: S7_LADDER    (16968-17056)  — Ladder/DCA level entries
  B14: S12_HA_TREND (17180-17222)  — HA trend color confirmation
  B15: S13_STRONG   (17223-17271)  — Strong trend continuation

Blocks 5/6/7 (alignment, HTF direction, trend gate) are BLOCKERS tested separately.
"""
import sys
import time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from v8_quick_engine import QuickConfig, load_npz, _safe, _safeb, _ha_int, compute_exit_signals

FAST_CRYPTO = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,UNIUSDT"


def _entry_wt_2of3(npz, n, is_long):
    wt1_3m = _safe(npz, 'wt1_3m', n); wt2_3m = _safe(npz, 'wt2_3m', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    if is_long:
        fav = (wt1_3m > wt2_3m).astype(int) + (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int)
    else:
        fav = (wt1_3m < wt2_3m).astype(int) + (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int)
    return fav >= 2


def _entry_bc156_bottom(npz, n, is_long):
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_15m = _safe(npz, 'wt_velocity_15m', n)
    wt_bull = _safeb(npz, 'wt_bullish_3m', n).astype(int) + _safeb(npz, 'wt_bullish_15m', n).astype(int) + _safeb(npz, 'wt_bullish_1h', n).astype(int) + _safeb(npz, 'wt_bullish_4h', n).astype(int)
    if not is_long:
        wt_bull = (~_safeb(npz, 'wt_bullish_3m', n)).astype(int) + (~_safeb(npz, 'wt_bullish_15m', n)).astype(int) + (~_safeb(npz, 'wt_bullish_1h', n)).astype(int) + (~_safeb(npz, 'wt_bullish_4h', n)).astype(int)
    if is_long:
        bounce = (wt1_15m < -20) & (wt_vel_15m > 0)
        trend_ok = wt1_1h > wt2_1h
    else:
        bounce = (wt1_15m > 20) & (wt_vel_15m < 0)
        trend_ok = wt1_1h < wt2_1h
    return bounce & trend_ok & (wt_bull >= 2)


def _entry_dc_retest(npz, n, is_long):
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0: close = _safe(npz, 'close_5m', n)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50)
    d_3m = _safe(npz, 'stoch_d_3m', n, 50)
    dc_high_4h = _safe(npz, 'dc_high_4h', n)
    dc_low_4h = _safe(npz, 'dc_low_4h', n)
    dc_high_4h_prev = np.roll(dc_high_4h, 5); dc_high_4h_prev[:5] = dc_high_4h[:5]
    dc_low_4h_prev = np.roll(dc_low_4h, 5); dc_low_4h_prev[:5] = dc_low_4h[:5]
    if is_long:
        expanded = (dc_high_4h > dc_high_4h_prev * 1.015) & (dc_high_4h_prev > 0)
        pullback = (close < dc_high_4h_prev * 1.005) & (close > dc_high_4h_prev * 0.99)
        turning = (k_3m > d_3m) & (k_3m < 50)
        return expanded & pullback & turning
    else:
        expanded = (dc_low_4h < dc_low_4h_prev * 0.985) & (dc_low_4h_prev > 0)
        pullback = (close > dc_low_4h_prev * 0.995) & (close < dc_low_4h_prev * 1.01)
        turning = (k_3m < d_3m) & (k_3m > 50)
        return expanded & pullback & turning


def _entry_snapback(npz, n, is_long):
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0: close = _safe(npz, 'close_5m', n)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50)
    wt_vel_3m = _safe(npz, 'wt_velocity_3m', n)
    dc_low_15m = _safe(npz, 'dc_low_15m', n)
    dc_high_15m = _safe(npz, 'dc_high_15m', n)
    if is_long:
        near_low = (dc_low_15m > 0) & (close < dc_low_15m * 1.003)
        momentum = (wt_vel_3m > 0.5) & (k_3m < 30)
        return near_low & momentum
    else:
        near_high = (dc_high_15m > 0) & (close > dc_high_15m * 0.997)
        momentum = (wt_vel_3m < -0.5) & (k_3m > 70)
        return near_high & momentum


def _entry_stoch_reversal(npz, n, is_long):
    k_3m = _safe(npz, 'stoch_k_3m', n, 50)
    d_3m = _safe(npz, 'stoch_d_3m', n, 50)
    k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
    k_15m = _safe(npz, 'stoch_k_15m', n, 50)
    if is_long:
        cross_up = (k_3m_prev <= d_3m) & (k_3m > d_3m) & (k_3m < 25)
        trend = k_15m < 40
        return cross_up & trend
    else:
        cross_down = (k_3m_prev >= d_3m) & (k_3m < d_3m) & (k_3m > 75)
        trend = k_15m > 60
        return cross_down & trend


def _entry_dc_breakout(npz, n, is_long):
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0: close = _safe(npz, 'close_5m', n)
    dc_high_1h = _safe(npz, 'dc_high_1h', n)
    dc_low_1h = _safe(npz, 'dc_low_1h', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    if is_long:
        return (dc_high_1h > 0) & (close > dc_high_1h * 1.001) & (wt1_15m > wt2_15m)
    else:
        return (dc_low_1h > 0) & (close < dc_low_1h * 0.999) & (wt1_15m < wt2_15m)


def _entry_wt_momentum(npz, n, is_long):
    wt1_3m = _safe(npz, 'wt1_3m', n); wt2_3m = _safe(npz, 'wt2_3m', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_3m = _safe(npz, 'wt_velocity_3m', n)
    if is_long:
        aligned = (wt1_3m > wt2_3m) & (wt1_15m > wt2_15m) & (wt1_1h > wt2_1h)
        accel = wt_vel_3m > 1.0
        return aligned & accel
    else:
        aligned = (wt1_3m < wt2_3m) & (wt1_15m < wt2_15m) & (wt1_1h < wt2_1h)
        accel = wt_vel_3m < -1.0
        return aligned & accel


def _entry_ha_trend(npz, n, is_long):
    ha_3m = _ha_int(npz, 'ha_3m', n)
    ha_15m = _ha_int(npz, 'ha_15m', n)
    ha_1h = _ha_int(npz, 'ha_1h', n)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50)
    if is_long:
        return (ha_3m == 1) & (ha_15m == 1) & (ha_1h == 1) & (k_3m < 60)
    else:
        return (ha_3m == -1) & (ha_15m == -1) & (ha_1h == -1) & (k_3m > 40)


def _entry_strong_trend(npz, n, is_long):
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0: close = _safe(npz, 'close_5m', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n)
    dc_low_4h = _safe(npz, 'dc_low_4h', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    if is_long:
        above_dc = (dc_high_4h > 0) & (close > dc_high_4h)
        strong_vel = wt_vel_1h > 2.0
        not_exhausted = k_1h < 85
        return above_dc & strong_vel & not_exhausted
    else:
        below_dc = (dc_low_4h > 0) & (close < dc_low_4h)
        strong_vel = wt_vel_1h < -2.0
        not_exhausted = k_1h > 15
        return below_dc & strong_vel & not_exhausted


BLOCKS = {
    "B01_WT_2of3": _entry_wt_2of3,
    "B02_BC156_BOTTOM": _entry_bc156_bottom,
    "B04_DC_RETEST": _entry_dc_retest,
    "B09_SNAPBACK": _entry_snapback,
    "B10_STOCH_REV": _entry_stoch_reversal,
    "B11_DC_BREAK": _entry_dc_breakout,
    "B12_WT_MOM": _entry_wt_momentum,
    "B14_HA_TREND": _entry_ha_trend,
    "B15_STRONG_TREND": _entry_strong_trend,
}


def simulate_with_block(stores, block_fn, cfg):
    from v8_quick_engine import compute_exit_signals
    all_pnl = []
    for sym, npz in stores.items():
        ts = npz.get('timestamps', npz.get('timestamp_3m', np.array([])))
        n = len(ts)
        if n < 100: continue
        close = _safe(npz, 'close_3m', n)
        if close.sum() == 0: close = _safe(npz, 'close_5m', n)
        for is_long in [True, False]:
            entry = block_fn(npz, n, is_long)
            exit_sig = compute_exit_signals(npz, n, is_long, cfg)
            in_pos = False; ep = 0.0; cd = 0
            for i in range(n):
                if cd > 0: cd -= 1; continue
                px = close[i]
                if px <= 0: continue
                if not in_pos and entry[i]:
                    in_pos = True; ep = px
                elif in_pos and exit_sig[i]:
                    pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    all_pnl.append(pnl)
                    in_pos = False; cd = 3
    if len(all_pnl) < 2:
        return {"sharpe": 0, "trades": len(all_pnl), "pnl": 0, "wr": 0, "avg": 0}
    p = np.array(all_pnl)
    w = int((p > 0).sum())
    m, s = p.mean(), p.std()
    return {
        "sharpe": round(m / s if s > 0 else 0, 4),
        "trades": len(p),
        "pnl": round(p.sum() / 100 * 2000, 2),
        "wr": round(w / len(p) * 100, 1),
        "avg": round(m, 4),
    }


def simulate_combined(stores, block_fns, cfg):
    from v8_quick_engine import compute_exit_signals
    all_pnl = []
    for sym, npz in stores.items():
        ts = npz.get('timestamps', npz.get('timestamp_3m', np.array([])))
        n = len(ts)
        if n < 100: continue
        close = _safe(npz, 'close_3m', n)
        if close.sum() == 0: close = _safe(npz, 'close_5m', n)
        for is_long in [True, False]:
            entry = np.zeros(n, dtype=bool)
            for fn in block_fns:
                entry |= fn(npz, n, is_long)
            exit_sig = compute_exit_signals(npz, n, is_long, cfg)
            in_pos = False; ep = 0.0; cd = 0
            for i in range(n):
                if cd > 0: cd -= 1; continue
                px = close[i]
                if px <= 0: continue
                if not in_pos and entry[i]:
                    in_pos = True; ep = px
                elif in_pos and exit_sig[i]:
                    pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    all_pnl.append(pnl)
                    in_pos = False; cd = 3
    if len(all_pnl) < 2:
        return {"sharpe": 0, "trades": len(all_pnl), "pnl": 0, "wr": 0, "avg": 0}
    p = np.array(all_pnl)
    w = int((p > 0).sum())
    m, s = p.mean(), p.std()
    return {
        "sharpe": round(m / s if s > 0 else 0, 4),
        "trades": len(p),
        "pnl": round(p.sum() / 100 * 2000, 2),
        "wr": round(w / len(p) * 100, 1),
        "avg": round(m, 4),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="crypto")
    parser.add_argument("--symbols", default="fast")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--npz-dir", default="")
    args = parser.parse_args()

    symbols = FAST_CRYPTO.split(",") if args.symbols == "fast" else [s.strip() for s in args.symbols.split(",")]
    stores = load_npz(args.mode, symbols, args.start, args.npz_dir)
    if not stores:
        print("No data"); return

    cfg = QuickConfig()
    if args.mode == "tradier":
        cfg.apply_tradier_defaults()

    print(f"\n{'='*95}")
    print(f"  REENTRY MICRO-ABLATION — {args.mode} | {len(stores)} symbols | start={args.start}")
    print(f"{'='*95}\n")
    print(f"{'Block':<25} {'Sharpe':>8} {'Trades':>8} {'PnL':>12} {'WR':>6} {'Avg%':>8} {'Verdict':>10}")
    print("-" * 82)

    results = {}
    for name, fn in BLOCKS.items():
        t0 = time.time()
        r = simulate_with_block(stores, fn, cfg)
        dt = time.time() - t0
        results[name] = r
        v = "KEEP ✓" if r["sharpe"] > 0.05 else ("WEAK ~" if r["sharpe"] > 0 else "DITCH ✗")
        print(f"{name:<25} {r['sharpe']:>8.4f} {r['trades']:>8} {r['pnl']:>12.2f} {r['wr']:>5.1f}% {r['avg']:>7.4f}% {v:>10}  ({dt:.1f}s)")

    # === ADDITIVE TEST: start with best block, add each one, keep if Sharpe improves ===
    print(f"\n{'='*95}")
    print("  ADDITIVE BUILD-UP (greedy: add block if Sharpe improves)")
    print(f"{'='*95}\n")
    ranked = sorted(results.items(), key=lambda x: x[1]["sharpe"], reverse=True)
    kept = []
    kept_fns = []
    best_sharpe = 0
    for name, r in ranked:
        test_fns = kept_fns + [BLOCKS[name]]
        combo = simulate_combined(stores, test_fns, cfg)
        if combo["sharpe"] > best_sharpe + 0.001:
            kept.append(name)
            kept_fns.append(BLOCKS[name])
            best_sharpe = combo["sharpe"]
            print(f"  + {name:<22} → Sharpe {combo['sharpe']:.4f} trades={combo['trades']} wr={combo['wr']}% ✓ ADDED")
        else:
            print(f"  - {name:<22} → Sharpe {combo['sharpe']:.4f} (was {best_sharpe:.4f}) ✗ SKIP")

    print(f"\n  FINAL: {len(kept)} blocks kept, Sharpe {best_sharpe:.4f}")
    print(f"  KEPT: {', '.join(kept)}")
    print(f"  DITCHED: {', '.join(name for name, _ in ranked if name not in kept)}")

    # === SWITCH RECOMMENDATIONS ===
    print(f"\n{'='*60}")
    print("  CONFIG SWITCH RECOMMENDATIONS")
    print(f"{'='*60}")
    for name, r in sorted(results.items()):
        status = "ON" if name in kept else "OFF"
        switch = f"REENTRY_{name}_ENABLED"
        print(f"  {switch:<40} = {'True' if status == 'ON' else 'False'}  # Sharpe {r['sharpe']:+.4f}, {r['trades']} trades")


if __name__ == "__main__":
    main()
