#!/usr/bin/env python3
"""TRENDER + BREAKOUT condition test on the VECTORIZED engine (vec_mass_scan primitives).

Reproduces the canonical baseline combos from vec_validate.CRYPTO_TOP_COMBOS,
then re-evaluates each baseline WITH TRENDER and WITH BREAKOUT added as a new
AND-condition. Reports Sharpe / WR / mean / DD per (combo, horizon) so you can
see whether the new gates improve, hurt, or are neutral.

The engine is pure-numpy on NPZ arrays — same vectorization as vec_mass_scan.
NO position simulation, NO Sharpe-from-equity tricks. metrics_guard sample-floor
applies (rows below floor get [DIAGNOSTIC]).

Usage:
    python vec_trender_breakout.py --mode crypto --bars 60000 \\
        --symbols-only-crypto --out data/sweep_results/inject_audit_vec_$(date -u +%Y%m%dT%H%M).csv
"""
import argparse, csv, json, math, os, sys, time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import build_crypto_conditions, build_tradier_conditions, fwd_returns, score, stack_field
from metrics_guard import write_sharpe_row, MIN_SYMS_CRYPTO, MIN_YEARS

CRYPTO_50_SYMS = [
    # 50 legacy USDT (per CLAUDE.md)
    "1INCHUSDT","ALGOUSDT","ANKRUSDT","ATOMUSDT","AXSUSDT","BANDUSDT","BATUSDT","BELUSDT","C98USDT","CELRUSDT",
    "CHRUSDT","COMPUSDT","COTIUSDT","DASHUSDT","DOTUSDT","EGLDUSDT","ENJUSDT","ETCUSDT","GRTUSDT","GTCUSDT",
    "HOTUSDT","IOSTUSDT","IOTAUSDT","IOTXUSDT","KAVAUSDT","KNCUSDT","KSMUSDT","LRCUSDT","MANAUSDT","MTLUSDT",
    "NKNUSDT","QTUMUSDT","RLCUSDT","RVNUSDT","SANDUSDT","SKLUSDT","SNXUSDT","STORJUSDT","SUSHIUSDT","SXPUSDT",
    "THETAUSDT","TRXUSDT","VETUSDT","XLMUSDT","XMRUSDT","XTZUSDT","YFIUSDT","ZENUSDT",
    # 10 USDC majors
    "BTCUSDC","ETHUSDC","SOLUSDC","BNBUSDC","XRPUSDC","ADAUSDC","AVAXUSDC","LINKUSDC","LTCUSDC","UNIUSDC",
]


def load_filtered(npz_dir: Path, symbols: list, max_bars: int, min_bars: int = 5000):
    """Load NPZ only for the given symbols, truncated to max_bars from the tail."""
    loaded = {}
    skipped = []
    for sym in symbols:
        f = npz_dir / f"{sym}.npz"
        if not f.exists():
            skipped.append(f"{sym}:missing"); continue
        try:
            z = dict(np.load(str(f), allow_pickle=True))
        except Exception as e:
            skipped.append(f"{sym}:bad({e.__class__.__name__})"); continue
        if "close" not in z: skipped.append(f"{sym}:no_close"); continue
        n = len(z["close"])
        if n < min_bars: skipped.append(f"{sym}:short({n})"); continue
        # Truncate tail-to-max_bars per file
        if n > max_bars:
            for k in list(z.keys()):
                arr = z[k]
                if hasattr(arr, "shape") and arr.shape and arr.shape[0] == n:
                    z[k] = arr[-max_bars:]
        loaded[sym] = z
    if not loaded: return loaded, 0, skipped
    # Align to common length (tail)
    min_len = min(len(z["close"]) for z in loaded.values())
    for sym, z in loaded.items():
        n = len(z["close"])
        if n > min_len:
            for k in list(z.keys()):
                arr = z[k]
                if hasattr(arr, "shape") and arr.shape and arr.shape[0] == n:
                    z[k] = arr[-min_len:]
    return loaded, min_len, skipped


def build_trender_breakout_conditions(loaded, base_tf_minutes: int = 3):
    """Add TRENDER + BREAKOUT boolean masks onto the existing condition library.
    Returns dict with new keys (L_trender_*, S_trender_*, L_breakout_*, S_breakout_*).
    """
    close = stack_field(loaded, "close", 0)        # (T, N) base TF (3m for crypto)
    # Try multiple volume sources — prefer 1h aggregated, fallback to base
    vol_15 = stack_field(loaded, "volume_15m", 0)
    # Bar counts: 1h = 60/3 = 20 bars; 4h = 80; 24h = 480
    bars_1h = 60 // base_tf_minutes
    bars_4h = 4 * bars_1h
    bars_24h = 24 * bars_1h

    T, N = close.shape
    # Forward-only shifted windows; nan for unavailable (early bars)
    def shift_back(arr, k):
        """Returns arr at index t-k; fills early bars with the first valid value."""
        out = np.empty_like(arr)
        out[:k] = arr[:1]  # propagate first row to fill (avoids div-by-zero)
        out[k:] = arr[:-k]
        return out

    c1h = shift_back(close, bars_1h)
    c4h = shift_back(close, bars_4h)
    c24h = shift_back(close, bars_24h)
    # Returns in DECIMAL (0.025 = 2.5%)
    safe = np.where(c1h > 0, c1h, 1)
    ret_1h = np.where(c1h > 0, close / safe - 1.0, 0.0)
    safe = np.where(c4h > 0, c4h, 1)
    ret_4h = np.where(c4h > 0, close / safe - 1.0, 0.0)
    safe = np.where(c24h > 0, c24h, 1)
    ret_24h = np.where(c24h > 0, close / safe - 1.0, 0.0)

    # Rolling 24h peak/trough — vectorized via stride trick
    from numpy.lib.stride_tricks import sliding_window_view
    win = bars_24h
    if T < win + 10:
        # Too few bars — fill flat
        peak_24h = close.copy(); trough_24h = close.copy()
    else:
        # sliding window over axis 0; result shape (T - win + 1, N, win) per axis spec
        sw = sliding_window_view(close, window_shape=win, axis=0)
        peak_tail = sw.max(axis=2)   # (T - win + 1, N)
        trough_tail = sw.min(axis=2)
        peak_24h = np.empty_like(close); trough_24h = np.empty_like(close)
        peak_24h[:win-1] = close[:win-1]
        trough_24h[:win-1] = close[:win-1]
        peak_24h[win-1:] = peak_tail
        trough_24h[win-1:] = trough_tail
    # DD: peak→now / peak * 100  (in PERCENT for absolute comparison)
    dd_long = np.where(peak_24h > 0, (peak_24h - close) / peak_24h, 0.0)  # decimal
    dd_short = np.where(trough_24h > 0, (close - trough_24h) / trough_24h, 0.0)
    # DD ratio = dd_long / max(ret_24h, 0.0001) — for LONG side
    dd_ratio_long = np.where(ret_24h > 0.0001, dd_long / ret_24h, 1.0)
    dd_ratio_short = np.where(ret_24h < -0.0001, dd_short / np.abs(ret_24h), 1.0)

    # Linearity proxy: |Pearson r| of close vs index over last 96 bars (≈ last 5h at 3m)
    # Using the closed-form for r when x = arange(N): r = sum((y - ȳ)(x - x̄)) / sqrt(...)
    lin_win = min(96, T // 4)
    if T < lin_win + 10:
        lin = np.zeros_like(close)
    else:
        sw_close = sliding_window_view(close, window_shape=lin_win, axis=0)
        x = np.arange(lin_win, dtype=np.float64)
        x_mean = x.mean(); x_dev = x - x_mean; x_var = (x_dev**2).sum()
        y = sw_close.astype(np.float64)
        y_mean = y.mean(axis=2, keepdims=True)
        y_dev = y - y_mean
        num = (y_dev * x_dev).sum(axis=2)  # (T-win+1, N)
        denom = np.sqrt(((y_dev**2).sum(axis=2)) * x_var)
        r = np.where(denom > 0, num / denom, 0.0)
        lin = np.empty_like(close)
        lin[:lin_win-1] = 0.0
        lin[lin_win-1:] = np.abs(r)

    # 24h quote volume USD = sum over last 96 (15m) bars of (volume_15m × close_15m_at_that_bar)
    # We have volume_15m forward-filled to base TF — that's redundant per-base-bar. Better: use
    # the per-bar volume_15m × close_15m_prev as an approximation. For simplicity sum over 480 base bars.
    # close_15m exists; if not, fall back to close.
    c15m = stack_field(loaded, "close_15m", 0)
    if c15m.shape != close.shape: c15m = close
    qv_per_bar = vol_15 * c15m
    if T < bars_24h + 10:
        qv_24h_usd = np.zeros_like(close)
    else:
        sw_qv = sliding_window_view(qv_per_bar, window_shape=bars_24h, axis=0)
        qv_24h_tail = sw_qv.sum(axis=2)
        # Divide by 5 because volume_15m is forward-filled to base 3m bars (5 base bars per 15m bar)
        # Actually fwd-fill duplicates the SAME 15m volume across 5 base bars. Sum over 480 bars
        # would 5x overcount. Approximate by dividing by 5.
        qv_24h_tail = qv_24h_tail / 5.0
        qv_24h_usd = np.empty_like(close)
        qv_24h_usd[:bars_24h-1] = 0.0
        qv_24h_usd[bars_24h-1:] = qv_24h_tail

    # Cross-sectional MAD outlier on ret_4h
    # At each row, median across syms (axis=1)
    med_4h_row = np.nanmedian(ret_4h, axis=1, keepdims=True)
    mad_4h_row = np.nanmedian(np.abs(ret_4h - med_4h_row), axis=1, keepdims=True)
    mad_4h_row = np.maximum(mad_4h_row, 0.003)  # 0.3% MAD floor

    C_new = {}
    # ── TRENDER variants (LONG) ────────────────────────────────────────────
    # Default + several knob combinations
    for lin_min, ret_min, qv_min_M, dd_max in [
        (0.55, 0.015, 25, 0.40),  # TRENDER_default
        (0.65, 0.025, 25, 0.40),  # TRENDER_tight (current LIVE defaults)
        (0.45, 0.010, 10, 0.55),  # TRENDER_loose
        (0.75, 0.040, 50, 0.30),  # TRENDER_strict
    ]:
        tag = f"L_trender_l{int(lin_min*100)}r{int(ret_min*1000)}q{qv_min_M}d{int(dd_max*100)}"
        C_new[tag] = (
            (lin >= lin_min) & (ret_24h >= ret_min) & (ret_4h >= 0) & (ret_1h >= 0) &
            (qv_24h_usd >= qv_min_M * 1_000_000) & (dd_ratio_long <= dd_max)
        )
        tag_s = f"S_trender_l{int(lin_min*100)}r{int(ret_min*1000)}q{qv_min_M}d{int(dd_max*100)}"
        C_new[tag_s] = (
            (lin >= lin_min) & (ret_24h <= -ret_min) & (ret_4h <= 0) & (ret_1h <= 0) &
            (qv_24h_usd >= qv_min_M * 1_000_000) & (dd_ratio_short <= dd_max)
        )
    # ── BREAKOUT variants (cross-sectional MAD outlier on ret_4h) ──────────
    for mad_mult, min_ret in [(2.0, 0.030), (2.0, 0.050), (1.5, 0.030), (2.5, 0.050), (3.0, 0.050)]:
        upper = med_4h_row + mad_mult * mad_4h_row
        lower = med_4h_row - mad_mult * mad_4h_row
        tag = f"L_breakout_m{mad_mult:g}r{int(min_ret*1000)}"
        C_new[tag] = (ret_4h >= upper) & (ret_4h >= min_ret)
        tag_s = f"S_breakout_m{mad_mult:g}r{int(min_ret*1000)}"
        C_new[tag_s] = (ret_4h <= lower) & (ret_4h <= -min_ret)
    return C_new, lin, ret_24h, qv_24h_usd


# Baselines from vec_validate.CRYPTO_TOP_COMBOS — pick=4 winners (no RSI per memory)
CRYPTO_BASELINES_PICK4 = [
    ("baseline_dc_dcpos_mfi1h_sma200", "L", ["dc_x1h", "dcpos_lt30", "mfi1h_lt40", "sma200up_D"], [4, 8, 16]),
    ("baseline_dc_dcpos_wt1h_wt4h",    "L", ["dc_x1h", "dcpos_lt30", "wt_1h", "wt_4h"], [16, 32]),
    ("baseline_dc_dcpos_wt1h_wtD",     "L", ["dc_x1h", "dcpos_lt30", "wt_1h", "wt_D"], [16, 32]),
    ("baseline_dc_mfi15_momwt_wt1h",   "L", ["dc_x1h", "mfi15_lt40", "mom_wt_all3", "wt_1h"], [16, 32]),
    ("baseline_dc_dcpos_mfi15_wt1h",   "L", ["dc_x1h", "dcpos_lt30", "mfi15_lt40", "wt_1h"], [16, 32]),
]


def build_extended_combos(C_new_keys):
    """Build the full evaluation list: each baseline × {alone, +TRENDER variants, +BREAKOUT variants}."""
    out = []
    out.extend(CRYPTO_BASELINES_PICK4)  # baseline-alone for parity check
    # Pick 1 representative TRENDER and BREAKOUT to combine (the tight defaults)
    add_keys_long = [k for k in C_new_keys if k.startswith("L_trender_") or k.startswith("L_breakout_")]
    for name, side, base_keys, horizons in CRYPTO_BASELINES_PICK4:
        for ak in add_keys_long:
            new_name = f"{name}+{ak.replace('L_','')}"
            out.append((new_name, side, base_keys + [ak], horizons))
    # Standalone TRENDER + BREAKOUT (no baseline)
    for ak in add_keys_long:
        out.append((f"alone_{ak.replace('L_','')}", "L", [ak], [4, 8, 16, 32]))
    return out


def evaluate_combo(C, fwd_dict, side, base_keys, horizons, min_trades=200):
    """Returns dict of {h: score_dict} for this combo across requested horizons."""
    # Build mask: AND of (L_/S_-prefixed) base_keys and any new injector keys
    mask = None
    for k in base_keys:
        # Prefix-aware key resolution
        full = k if k in C else (f"{side}_{k}" if f"{side}_{k}" in C else None)
        if full is None: return {"error": f"missing_key:{k}"}
        m = C[full]
        mask = m if mask is None else (mask & m)
    if mask is None: return {"error": "no_keys"}
    out = {}
    for h in horizons:
        if h not in fwd_dict: continue
        s = score(mask, fwd_dict[h], min_trades)
        if s is not None: out[h] = s
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="crypto")
    ap.add_argument("--bars", type=int, default=60000, help="max bars per sym (3m base TF for crypto = 60000 ≈ 4.1 mo)")
    ap.add_argument("--min-bars", type=int, default=5000)
    ap.add_argument("--min-trades", type=int, default=200, help="min trades per (combo, horizon) to publish a row")
    ap.add_argument("--npz-dir", default="/home/niels/binance-sandbox/backtest_v8/indicators")
    ap.add_argument("--symbols", default="", help="comma-separated; empty = use built-in CRYPTO_50_SYMS")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.mode != "crypto":
        print("This script is crypto-only for now"); return

    syms = CRYPTO_50_SYMS if not args.symbols else [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"[VEC_TB] symbols={len(syms)} bars={args.bars}", flush=True)
    t0 = time.time()
    loaded, n_bars, skipped = load_filtered(Path(args.npz_dir), syms, args.bars, args.min_bars)
    print(f"[VEC_TB] {time.time()-t0:.1f}s loaded {len(loaded)} syms × {n_bars} bars (skipped {len(skipped)})", flush=True)
    if skipped: print(f"  skip detail: {skipped[:5]}{'…' if len(skipped)>5 else ''}", flush=True)
    if len(loaded) < 8:
        print("[VEC_TB] ABORT: need ≥8 syms"); return

    builder = build_crypto_conditions if args.mode == "crypto" else build_tradier_conditions
    C, close = builder(loaded)
    print(f"[VEC_TB] {time.time()-t0:.1f}s built {len(C)} base conditions", flush=True)
    C_new, lin_arr, ret24_arr, qv_arr = build_trender_breakout_conditions(loaded, base_tf_minutes=3)
    print(f"[VEC_TB] {time.time()-t0:.1f}s built {len(C_new)} TRENDER+BREAKOUT conditions", flush=True)
    print(f"  lin range: [{lin_arr[lin_arr>0].min():.3f}, {lin_arr.max():.3f}] mean={lin_arr.mean():.3f}", flush=True)
    print(f"  ret_24h range: [{ret24_arr.min()*100:.1f}%, {ret24_arr.max()*100:.1f}%]", flush=True)
    print(f"  qv_24h_usd range: [${qv_arr[qv_arr>0].min()/1e6:.1f}M, ${qv_arr.max()/1e6:.0f}M]", flush=True)
    C.update(C_new)

    horizons_needed = sorted({h for _,_,_,hs in CRYPTO_BASELINES_PICK4 for h in hs} | {4,8,16,32})
    fwd = fwd_returns(close, horizons_needed)
    print(f"[VEC_TB] {time.time()-t0:.1f}s fwd returns for {horizons_needed}", flush=True)

    combos = build_extended_combos(set(C_new.keys()))
    print(f"[VEC_TB] evaluating {len(combos)} combos × multiple horizons", flush=True)
    n_syms = len(loaded)
    base_tf_min = 3 if args.mode == "crypto" else 5
    years = n_bars * base_tf_min / 60 / 24 / 365.25

    # Output rows
    out_rows = []
    print(f"\n{'combo':<70} {'h':>4} {'n':>6} {'sharpe':>7} {'wr':>6} {'mean':>7} {'pf':>5}", flush=True)
    print("─"*110, flush=True)
    for name, side, base_keys, horizons in combos:
        results = evaluate_combo(C, fwd, side, base_keys, horizons, args.min_trades)
        if "error" in results:
            continue
        for h, r in sorted(results.items()):
            print(f"{name:<70} {h:>4d} {r['n']:>6d} {r['sharpe']:>7.4f} {r['wr']:>5.1f}% {r['mean']*100:>6.3f}% {r['pf']:>5.2f}", flush=True)
            out_rows.append({"name": name, "side": side, "horizon": h, **r, "n_syms": n_syms, "years": years})

    # Write canonical CSV via metrics_guard
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists(): out_path.unlink()
        from datetime import datetime, timezone
        ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for r in out_rows:
            n = r["n"]
            avg_gain_trade_pct = r["mean"] * 100  # convert decimal to %
            acc_gain_pct = avg_gain_trade_pct * n
            gain_per_yr = acc_gain_pct / max(years, 0.01)
            row = {
                "pool_sharpe": round(r["sharpe"], 4),
                "sym_sharpe": 0.0,  # not computed per-sym in this script (vec is pooled)
                "avg_gain_trade": round(avg_gain_trade_pct, 4),
                "gain_per_yr": round(gain_per_yr, 2),
                "gain_sym_yr": round(gain_per_yr / max(n_syms, 1), 4),
                "trades": n,
                "max_dd_pct": 0.0,  # vec doesn't sim equity — DD requires sequencing not done here
                "n_syms": n_syms,
                "years": round(years, 2),
                "ts_utc": ts_iso, "mode": args.mode,
                "iter": r["name"], "side": r["side"], "horizon": r["horizon"],
                "wr": round(r["wr"], 1), "pf": round(r["pf"], 3),
                "engine": "vec_trender_breakout",
            }
            try:
                write_sharpe_row(out_path, row, mode="crypto")
            except Exception as e:
                print(f"[METRICS] REFUSED {r['name']}@{r['horizon']}: {e}", flush=True)
        print(f"\n[VEC_TB] {time.time()-t0:.1f}s wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
