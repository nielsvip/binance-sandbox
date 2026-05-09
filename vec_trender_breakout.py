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


# Only the NPZ fields the engine actually uses — drops memory ~75% vs loading all 150+ fields.
NEEDED_FIELDS = (
    "close", "close_15m", "volume_15m",
    "stoch_k_5m", "stoch_k_15m", "stoch_k_1h", "stoch_k_4h", "stoch_k_D",
    "mfi_5m", "mfi_15m", "mfi_1h", "mfi_4h",
    "rsi_5m", "rsi_15m", "rsi_1h",
    "sma_200_D", "sma_200_1h", "sma_200_15m",
    "dc_position_15m", "dc_position_1h",
    "bb_pct_b_15m", "bb_pct_b_1h", "bb_pct_b_4h",
    "atr_pct_15m",
    "wt_bullish_5m", "wt_bullish_15m", "wt_bullish_1h", "wt_bullish_4h", "wt_bullish_D",
    "wt_cross_bull_5m", "wt_cross_bull_15m", "wt_cross_bull_1h",
    "wt_cross_bear_5m", "wt_cross_bear_15m",
    "stoch_crossover_5m", "stoch_crossover_15m", "stoch_crossover_1h",
    "stoch_crossunder_5m", "stoch_crossunder_15m",
    "dc_basis_crossover_5m", "dc_basis_crossover_15m", "dc_basis_crossover_1h", "dc_basis_crossover_4h",
    "dc_basis_crossunder_15m", "dc_basis_crossunder_1h",
)


def load_filtered(npz_dir: Path, symbols: list, max_bars: int, min_bars: int = 5000):
    """Load NPZ only for the given symbols, only NEEDED_FIELDS, truncated to max_bars from the tail."""
    loaded = {}
    skipped = []
    for sym in symbols:
        f = npz_dir / f"{sym}.npz"
        if not f.exists():
            skipped.append(f"{sym}:missing"); continue
        try:
            with np.load(str(f), allow_pickle=True) as raw:
                # Only pull NEEDED_FIELDS — major memory win at scale
                if "close" not in raw.files: skipped.append(f"{sym}:no_close"); continue
                n = len(raw["close"])
                if n < min_bars: skipped.append(f"{sym}:short({n})"); continue
                start = max(0, n - max_bars) if max_bars and n > max_bars else 0
                z = {}
                for k in NEEDED_FIELDS:
                    if k in raw.files:
                        arr = raw[k]
                        if hasattr(arr, "shape") and arr.shape and arr.shape[0] == n:
                            z[k] = np.array(arr[start:], copy=True, dtype=arr.dtype)
                        else:
                            z[k] = arr
        except Exception as e:
            skipped.append(f"{sym}:bad({e.__class__.__name__})"); continue
        loaded[sym] = z
    if not loaded: return loaded, 0, skipped
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

    # Rolling 24h peak/trough — bottleneck.move_max/move_min (in-place, ~32MB peak vs 15GB stride_tricks)
    import bottleneck as bn
    win = bars_24h
    if T < win + 10:
        peak_24h = close.copy(); trough_24h = close.copy()
    else:
        # bn.move_max needs float64; close is float32 → cast cheaply
        c64 = close.astype(np.float64, copy=False)
        peak_24h = bn.move_max(c64, window=win, axis=0).astype(np.float32)   # NaN for first win-1 bars
        trough_24h = bn.move_min(c64, window=win, axis=0).astype(np.float32)
        # Replace NaN warmup with current close so dd ratios stay 0 there (no false signals)
        peak_24h = np.where(np.isnan(peak_24h), close, peak_24h)
        trough_24h = np.where(np.isnan(trough_24h), close, trough_24h)
    # DD: peak→now / peak * 100  (in PERCENT for absolute comparison)
    dd_long = np.where(peak_24h > 0, (peak_24h - close) / peak_24h, 0.0)  # decimal
    dd_short = np.where(trough_24h > 0, (close - trough_24h) / trough_24h, 0.0)
    # DD ratio = dd_long / max(ret_24h, 0.0001) — for LONG side
    dd_ratio_long = np.where(ret_24h > 0.0001, dd_long / ret_24h, 1.0)
    dd_ratio_short = np.where(ret_24h < -0.0001, dd_short / np.abs(ret_24h), 1.0)

    # Linearity proxy: |Pearson r| of close vs linear index, rolling 96 bars.
    # Closed form: r = (sum(xy) - n·x̄·ȳ) / sqrt((sum(x²) - n·x̄²) · (sum(y²) - n·ȳ²))
    # x = arange(win) is constant per window so we can precompute x_mean and x_var.
    lin_win = min(96, T // 4)
    if T < lin_win + 10:
        lin = np.zeros_like(close)
    else:
        x = np.arange(lin_win, dtype=np.float64)
        x_mean = x.mean(); x_dev = x - x_mean; x_var = (x_dev**2).sum()
        # Compute rolling sums via bn (cheap O(N))
        c64 = close.astype(np.float64, copy=False)
        sum_y = bn.move_sum(c64, window=lin_win, axis=0)
        sum_yy = bn.move_sum(c64**2, window=lin_win, axis=0)
        # sum_xy needs y multiplied by current bar's x, but x is window-relative not absolute.
        # Trick: shift the c64 array by 0..lin_win-1 and multiply by x[i], sum.
        # Instead use the equivalence: cov(x,y) = E[xy] - E[x]E[y]; we have E[y]=sum_y/n,
        # but E[xy] needs the within-window cross. Do it directly with a cumulative approach.
        # For speed: compute weighted sum via convolve. ~30ms for our sizes.
        xw = x_dev[::-1]  # reversed because convolution flips
        from scipy.signal import fftconvolve
        sum_xdev_ydev = np.zeros_like(c64)
        for s in range(c64.shape[1]):
            ys = c64[:, s]
            ymean_roll = sum_y[:, s] / lin_win
            # rolling y_dev (per window): we need sum of (y_t - ȳ)·x_dev[idx_in_win]
            # = sum(y_t · x_dev) - ȳ · sum(x_dev) [latter is 0 by construction]
            # = sum over window of y_t weighted by x_dev[t mod win]
            # Use convolution of y with x_dev kernel
            conv = np.convolve(ys, xw, mode="full")[lin_win - 1: lin_win - 1 + len(ys)]
            sum_xdev_ydev[:, s] = conv
        # variance of y in each window
        var_y = sum_yy - (sum_y**2) / lin_win
        denom = np.sqrt(np.maximum(var_y * x_var, 1e-20))
        r = sum_xdev_ydev / denom
        lin = np.where(np.isnan(r), 0.0, np.abs(r)).astype(np.float32)

    # 24h quote volume USD: rolling sum of (volume_15m × close_15m), divided by 5 (fwd-fill correction)
    c15m = stack_field(loaded, "close_15m", 0)
    if c15m.shape != close.shape: c15m = close
    qv_per_bar = (vol_15 * c15m).astype(np.float64)
    if T < bars_24h + 10:
        qv_24h_usd = np.zeros_like(close)
    else:
        qv_24h_usd = (bn.move_sum(qv_per_bar, window=bars_24h, axis=0) / 5.0).astype(np.float32)
        qv_24h_usd = np.where(np.isnan(qv_24h_usd), 0.0, qv_24h_usd)

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


# Mean-reversion baselines (vec_validate.CRYPTO_TOP_COMBOS, translated to crypto keys).
# Fire on DC-cross-up FROM oversold + WT/MFI confirms — bottom-fishing reversals.
CRYPTO_BASELINES_MEANREV = [
    ("MR1_dc1h_dcpos15_mfi1h_smaD",   "L", ["dc_x1h", "dcpos15_lt30", "mfi1h_lt40", "sma200up_1h"], [4, 8, 16]),
    ("MR2_dc1h_dcpos15_wt1h_wt4h",    "L", ["dc_x1h", "dcpos15_lt30", "wt_1h", "wt_4h"], [16, 32]),
    ("MR3_dc1h_dcpos15_wt1h_wtD",     "L", ["dc_x1h", "dcpos15_lt30", "wt_1h", "wt_D"], [16, 32]),
    ("MR4_dc1h_mfi15_wtall3_wt1h",    "L", ["dc_x1h", "mfi15_lt40", "wt_all3", "wt_1h"], [16, 32]),
    ("MR5_dc1h_dcpos15_mfi15_wt1h",   "L", ["dc_x1h", "dcpos15_lt30", "mfi15_lt40", "wt_1h"], [16, 32]),
]
# Momentum-continuation baselines — same regime as TRENDER+BREAKOUT.
# Fire when DC crossed UP, HTF aligned bullish, momentum building.
CRYPTO_BASELINES_MOMENTUM = [
    ("MOM1_dcx1h_wt2of3_smaD",        "L", ["dc_x1h", "wt_2of3", "sma200up_1h"], [16, 32, 64]),
    ("MOM2_dcx1h_dcposup_smaD",       "L", ["dc_x1h", "mom_dcpos15_gt50", "sma200up_1h"], [16, 32, 64]),
    ("MOM3_wtx1h_wt2of3_smaD",        "L", ["wt_x1h", "wt_2of3", "sma200up_1h"], [16, 32, 64]),
    ("MOM4_dcx1h_mfiup_smaD_wt1h",    "L", ["dc_x1h", "mom_mfi15_gt50", "sma200up_1h", "wt_1h"], [16, 32, 64]),
    ("MOM5_dcx4h_wtall3_smaD",        "L", ["dc_x4h", "wt_all3", "sma200up_1h"], [32, 64, 128]),
    ("MOM6_dcx1h_bbup_smaD_wt4h",     "L", ["dc_x1h", "mom_bb15_gt70", "sma200up_1h", "wt_4h"], [16, 32]),
]
CRYPTO_BASELINES_PICK4 = CRYPTO_BASELINES_MEANREV  # legacy alias

# SHORT mirrors — symmetric structure on the bear side
CRYPTO_SHORT_BASELINES_MEANREV = [
    ("MR1S_dcxu1h_dcpos15g70_mfi1hg60_smaDdn", "S", ["dc_xu1h", "dcpos15_gt70", "mfi1h_gt60", "sma200dn_D"], [4, 8, 16]),
    ("MR2S_dcxu1h_dcpos15g70_notwt1h_notwt4h", "S", ["dc_xu1h", "dcpos15_gt70", "not_wt_1h", "not_wt_4h"], [16, 32]),
    ("MR3S_dcxu1h_dcpos15g70_notwt1h_notwtD",  "S", ["dc_xu1h", "dcpos15_gt70", "not_wt_1h", "not_wt_D"], [16, 32]),
    ("MR4S_dcxu1h_mfi15g60_notwtall3_notwt1h", "S", ["dc_xu1h", "mfi15_gt60", "not_wt_all3", "not_wt_1h"], [16, 32]),
    ("MR5S_dcxu1h_dcpos15g70_mfi15g60_notwt1h","S", ["dc_xu1h", "dcpos15_gt70", "mfi15_gt60", "not_wt_1h"], [16, 32]),
]
CRYPTO_SHORT_BASELINES_MOMENTUM = [
    ("MOM1S_dcxu1h_notwt2of3_smaDn1h",         "S", ["dc_xu1h", "not_wt_2of3", "sma200dn_1h"], [16, 32, 64]),
    ("MOM4S_dcxu1h_mfi15g60_smaDn1h_notwt1h",  "S", ["dc_xu1h", "mfi15_gt60", "sma200dn_1h", "not_wt_1h"], [16, 32, 64]),
    ("MOM6S_dcxu1h_dcpos15g70_smaDn1h_notwt4h","S", ["dc_xu1h", "dcpos15_gt70", "sma200dn_1h", "not_wt_4h"], [16, 32]),
]


def build_extended_combos(C_new_keys):
    """Build evaluation list:
      - each MR baseline alone
      - each MOM baseline alone
      - each MOM baseline + TRENDER (AND) — momentum + trender filter
      - each MOM baseline + BREAKOUT (AND) — momentum + breakout filter
      - each MR baseline OR (TRENDER|BREAKOUT) — union, see if injectors add new alpha
      - standalone TRENDER + BREAKOUT for reference
    """
    out = []
    # LONG baselines alone (4-tuple → 5-tuple with op)
    for n, s, k, h in CRYPTO_BASELINES_MEANREV: out.append((n, s, k, h, "AND"))
    for n, s, k, h in CRYPTO_BASELINES_MOMENTUM: out.append((n, s, k, h, "AND"))
    # SHORT baselines alone
    for n, s, k, h in CRYPTO_SHORT_BASELINES_MEANREV: out.append((n, s, k, h, "AND"))
    for n, s, k, h in CRYPTO_SHORT_BASELINES_MOMENTUM: out.append((n, s, k, h, "AND"))
    add_keys_long = [k for k in C_new_keys if k.startswith("L_trender_") or k.startswith("L_breakout_")]
    add_keys_short = [k for k in C_new_keys if k.startswith("S_trender_") or k.startswith("S_breakout_")]
    # MOM-LONG baselines + LONG injectors (same regime)
    for name, side, base_keys, horizons in CRYPTO_BASELINES_MOMENTUM:
        for ak in add_keys_long:
            out.append((f"{name}+{ak.replace('L_','')}", side, base_keys + [ak], horizons, "AND"))
    # MOM-SHORT baselines + SHORT injectors
    for name, side, base_keys, horizons in CRYPTO_SHORT_BASELINES_MOMENTUM:
        for ak in add_keys_short:
            out.append((f"{name}+{ak.replace('S_','')}", side, base_keys + [ak], horizons, "AND"))
    # Standalone LONG injectors
    for ak in add_keys_long:
        out.append((f"alone_{ak.replace('L_','')}", "L", [ak], [4, 8, 16, 32], "AND"))
    # Standalone SHORT injectors
    for ak in add_keys_short:
        out.append((f"alone_{ak.replace('S_','')}_S", "S", [ak], [4, 8, 16, 32], "AND"))
    return out


def _resolve_mask(C, side, keys):
    mask = None
    for k in keys:
        full = k if k in C else (f"{side}_{k}" if f"{side}_{k}" in C else None)
        if full is None: return None, f"missing_key:{k}"
        m = C[full]
        mask = m if mask is None else (mask & m)
    return mask, None


def evaluate_combo(C, fwd_dict, side, base_keys, horizons, min_trades=200, op="AND", or_keys=None):
    """Returns dict of {h: score_dict} for this combo across requested horizons.
    op="AND": mask = AND(base_keys)
    op="OR" : mask = AND(base_keys) | OR(or_keys)  (union of two AND-clauses)
    """
    base_mask, err = _resolve_mask(C, side, base_keys)
    if err: return {"error": err}
    if op == "OR" and or_keys:
        # OR each or_key into the union (each or_key is single-condition mask)
        union = base_mask.copy()
        for k in or_keys:
            full = k if k in C else (f"{side}_{k}" if f"{side}_{k}" in C else None)
            if full is None: return {"error": f"missing_or_key:{k}"}
            union = union | C[full]
        mask = union
    else:
        mask = base_mask
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
    print(f"\n{'combo':<78} {'op':>4} {'h':>4} {'n':>6} {'sharpe':>7} {'wr':>6} {'mean':>7} {'pf':>5}", flush=True)
    print("─"*120, flush=True)
    for tup in combos:
        # Tuple is (name, side, base_keys, horizons, op[, or_keys])
        name, side, base_keys, horizons, op = tup[0], tup[1], tup[2], tup[3], tup[4]
        or_keys = tup[5] if len(tup) > 5 else None
        results = evaluate_combo(C, fwd, side, base_keys, horizons, args.min_trades, op=op, or_keys=or_keys)
        if "error" in results: continue
        for h, r in sorted(results.items()):
            print(f"{name:<78} {op:>4s} {h:>4d} {r['n']:>6d} {r['sharpe']:>7.4f} {r['wr']:>5.1f}% {r['mean']*100:>6.3f}% {r['pf']:>5.2f}", flush=True)
            out_rows.append({"name": name, "side": side, "op": op, "horizon": h, **r, "n_syms": n_syms, "years": years})

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
