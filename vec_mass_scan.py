#!/usr/bin/env python3
"""Truly vectorized mass signal scan.
Small sample, millions of param combos, pure numpy, no position simulation.
Forward-return scoring across multiple horizons.
Separate condition banks for crypto vs tradier.
"""
import argparse
import csv
import itertools
import os
import sys
import time
from pathlib import Path

import numpy as np

CRYPTO_SYMS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC", "ADAUSDC"]
TRADIER_SYMS = ["AAPL", "MSFT", "NVDA", "AMZN", "SPY", "XOM", "GLD", "TSLA"]
HORIZONS = [4, 8, 16, 32, 64, 128, 256]


def detect_npz_dir(mode):
    for base in (Path("/home/niels/binance-sandbox"), Path("/Users/niels/Documents/binance")):
        for sub in (("backtest_v8", "indicators"), ("backtest_v4_tradier", "indicators") if mode == "tradier" else ("backtest_v4", "indicators")):
            d = base / sub[0] / sub[1]
            if d.exists() and any(d.glob("*.npz")):
                return d
    raise RuntimeError("No NPZ dir found")


def try_symbols(mode):
    if mode == "crypto":
        return CRYPTO_SYMS
    return TRADIER_SYMS


def load_slice(mode, symbols, n_bars):
    npz_dir = detect_npz_dir(mode)
    loaded = {}
    min_len = 10**9
    for s in symbols:
        p = npz_dir / f"{s}.npz"
        if not p.exists():
            alt = npz_dir / f"{s.replace('USDT','')}.npz"
            if alt.exists():
                p = alt
            else:
                continue
        z = dict(np.load(str(p), allow_pickle=True))
        loaded[s] = z
        min_len = min(min_len, len(z["close"]))
    if not loaded:
        raise RuntimeError(f"No NPZ loaded for {symbols} from {npz_dir}")
    use = min(n_bars, min_len)
    # Take most recent `use` bars per symbol
    for s in list(loaded.keys()):
        z = loaded[s]
        for k in list(z.keys()):
            a = np.asarray(z[k])
            if a.ndim == 1 and len(a) >= use:
                z[k] = a[-use:]
        loaded[s] = z
    return loaded, use, npz_dir


def stack_field(loaded, field, default=0.0):
    arrs = []
    for s in loaded:
        a = loaded[s].get(field)
        if a is None:
            n = len(loaded[s]["close"])
            a = np.full(n, default, dtype=np.float64)
        a = np.asarray(a, dtype=np.float64)
        arrs.append(a)
    return np.stack(arrs, axis=1)  # shape (bars, n_symbols)


def stack_bool(loaded, field):
    arrs = []
    for s in loaded:
        a = loaded[s].get(field)
        if a is None:
            n = len(loaded[s]["close"])
            a = np.zeros(n, dtype=bool)
        arrs.append(np.asarray(a, dtype=bool))
    return np.stack(arrs, axis=1)


def build_crypto_conditions(loaded):
    C = {}
    k5 = stack_field(loaded, "stoch_k_5m", 50)
    k15 = stack_field(loaded, "stoch_k_15m", 50)
    k1h = stack_field(loaded, "stoch_k_1h", 50)
    k4h = stack_field(loaded, "stoch_k_4h", 50)
    kD = stack_field(loaded, "stoch_k_D", 50)
    mfi5 = stack_field(loaded, "mfi_5m", 50)
    mfi15 = stack_field(loaded, "mfi_15m", 50)
    mfi1h = stack_field(loaded, "mfi_1h", 50)
    mfi4h = stack_field(loaded, "mfi_4h", 50)
    rsi5 = stack_field(loaded, "rsi_5m", 50)
    rsi15 = stack_field(loaded, "rsi_15m", 50)
    rsi1h = stack_field(loaded, "rsi_1h", 50)
    close = stack_field(loaded, "close", 0)
    sma200D = stack_field(loaded, "sma_200_D", 0)
    sma200_1h = stack_field(loaded, "sma_200_1h", 0)
    sma200_15m = stack_field(loaded, "sma_200_15m", 0)
    dc_pos15 = stack_field(loaded, "dc_position_15m", 0.5)
    dc_pos1h = stack_field(loaded, "dc_position_1h", 0.5)
    bb_pct_15m = stack_field(loaded, "bb_pct_b_15m", 0.5)
    bb_pct_1h = stack_field(loaded, "bb_pct_b_1h", 0.5)
    bb_pct_4h = stack_field(loaded, "bb_pct_b_4h", 0.5)
    atr_pct_15m = stack_field(loaded, "atr_pct_15m", 0)

    wtb_5 = stack_bool(loaded, "wt_bullish_5m")
    wtb_15 = stack_bool(loaded, "wt_bullish_15m")
    wtb_1h = stack_bool(loaded, "wt_bullish_1h")
    wtb_4h = stack_bool(loaded, "wt_bullish_4h")
    wtb_D = stack_bool(loaded, "wt_bullish_D")
    wtc_5 = stack_bool(loaded, "wt_cross_bull_5m")
    wtc_15 = stack_bool(loaded, "wt_cross_bull_15m")
    wtc_1h = stack_bool(loaded, "wt_cross_bull_1h")
    wtcr_5 = stack_bool(loaded, "wt_cross_bear_5m")
    wtcr_15 = stack_bool(loaded, "wt_cross_bear_15m")
    stc_5 = stack_bool(loaded, "stoch_crossover_5m")
    stc_15 = stack_bool(loaded, "stoch_crossover_15m")
    stc_1h = stack_bool(loaded, "stoch_crossover_1h")
    stcu_5 = stack_bool(loaded, "stoch_crossunder_5m")
    stcu_15 = stack_bool(loaded, "stoch_crossunder_15m")
    dcx_5 = stack_bool(loaded, "dc_basis_crossover_5m")
    dcx_15 = stack_bool(loaded, "dc_basis_crossover_15m")
    dcx_1h = stack_bool(loaded, "dc_basis_crossover_1h")
    dcx_4h = stack_bool(loaded, "dc_basis_crossover_4h")
    dcxu_15 = stack_bool(loaded, "dc_basis_crossunder_15m")
    dcxu_1h = stack_bool(loaded, "dc_basis_crossunder_1h")

    # LONG — stoch thresholds × TFs (many variants)
    for tf, ka in [("5", k5), ("15", k15), ("1h", k1h), ("4h", k4h), ("D", kD)]:
        for thr in (20, 30, 40, 50, 60):
            C[f"L_k{tf}_lt{thr}"] = ka < thr
    # LONG — MFI thresholds × TFs
    for tf, ma in [("5", mfi5), ("15", mfi15), ("1h", mfi1h), ("4h", mfi4h)]:
        for thr in (20, 30, 40, 50):
            C[f"L_mfi{tf}_lt{thr}"] = ma < thr
    # LONG — RSI thresholds × TFs
    for tf, ra in [("5", rsi5), ("15", rsi15), ("1h", rsi1h)]:
        for thr in (25, 30, 35, 40, 45):
            C[f"L_rsi{tf}_lt{thr}"] = ra < thr
    # LONG — DC position
    for thr in (10, 20, 30, 40, 50):
        C[f"L_dcpos15_lt{thr}"] = dc_pos15 < (thr / 100.0)
        C[f"L_dcpos1h_lt{thr}"] = dc_pos1h < (thr / 100.0)
    # LONG — BB percent B
    for thr in (0, 10, 20, 30):
        C[f"L_bb15_lt{thr}"] = bb_pct_15m < (thr / 100.0)
        C[f"L_bb1h_lt{thr}"] = bb_pct_1h < (thr / 100.0)
    # LONG — WT bullish flags
    C["L_wt_5m"] = wtb_5
    C["L_wt_15m"] = wtb_15
    C["L_wt_1h"] = wtb_1h
    C["L_wt_4h"] = wtb_4h
    C["L_wt_D"] = wtb_D
    C["L_wt_all3"] = wtb_1h & wtb_4h & wtb_D
    C["L_wt_2of3"] = (wtb_1h.astype(int) + wtb_4h.astype(int) + wtb_D.astype(int)) >= 2
    C["L_wt_ltf_both"] = wtb_5 & wtb_15
    # LONG — WT/stoch/DC crosses (triggers)
    C["L_wt_x5"] = wtc_5
    C["L_wt_x15"] = wtc_15
    C["L_wt_x1h"] = wtc_1h
    C["L_stoch_x5"] = stc_5
    C["L_stoch_x15"] = stc_15
    C["L_stoch_x1h"] = stc_1h
    C["L_dc_x5"] = dcx_5
    C["L_dc_x15"] = dcx_15
    C["L_dc_x1h"] = dcx_1h
    C["L_dc_x4h"] = dcx_4h
    # LONG — trend context
    C["L_sma200up_D"] = (sma200D > 0) & (close > sma200D)
    C["L_sma200up_1h"] = (sma200_1h > 0) & (close > sma200_1h)
    C["L_sma200up_15m"] = (sma200_15m > 0) & (close > sma200_15m)
    # LONG — momentum continuation
    C["L_mom_mfi15_gt50"] = mfi15 > 50
    C["L_mom_mfi1h_gt60"] = mfi1h > 60
    C["L_mom_dcpos15_gt50"] = dc_pos15 > 0.5
    C["L_mom_dcpos15_gt70"] = dc_pos15 > 0.7
    C["L_mom_bb15_gt70"] = bb_pct_15m > 0.7
    C["L_atr_gt1"] = atr_pct_15m > 1.0
    C["L_atr_lt2"] = (atr_pct_15m < 2.0) & (atr_pct_15m > 0)

    # SHORT mirrors — stoch thresholds
    for tf, ka in [("5", k5), ("15", k15), ("1h", k1h), ("4h", k4h), ("D", kD)]:
        for thr in (40, 50, 60, 70, 80):
            C[f"S_k{tf}_gt{thr}"] = ka > thr
    # SHORT — MFI thresholds
    for tf, ma in [("5", mfi5), ("15", mfi15), ("1h", mfi1h), ("4h", mfi4h)]:
        for thr in (50, 60, 70, 80):
            C[f"S_mfi{tf}_gt{thr}"] = ma > thr
    # SHORT — RSI thresholds
    for tf, ra in [("5", rsi5), ("15", rsi15), ("1h", rsi1h)]:
        for thr in (55, 60, 65, 70, 75):
            C[f"S_rsi{tf}_gt{thr}"] = ra > thr
    # SHORT — DC position upper
    for thr in (50, 60, 70, 80, 90):
        C[f"S_dcpos15_gt{thr}"] = dc_pos15 > (thr / 100.0)
    # SHORT — BB percent B upper
    for thr in (70, 80, 90, 100):
        C[f"S_bb15_gt{thr}"] = bb_pct_15m > (thr / 100.0)
        C[f"S_bb1h_gt{thr}"] = bb_pct_1h > (thr / 100.0)
    # SHORT — WT not bullish
    C["S_not_wt_5m"] = ~wtb_5
    C["S_not_wt_15m"] = ~wtb_15
    C["S_not_wt_1h"] = ~wtb_1h
    C["S_not_wt_4h"] = ~wtb_4h
    C["S_not_wt_D"] = ~wtb_D
    C["S_not_wt_all3"] = (~wtb_1h) & (~wtb_4h) & (~wtb_D)
    C["S_not_wt_2of3"] = ((~wtb_1h).astype(int) + (~wtb_4h).astype(int) + (~wtb_D).astype(int)) >= 2
    # SHORT — bearish crosses
    C["S_wt_xr5"] = wtcr_5
    C["S_wt_xr15"] = wtcr_15
    C["S_stoch_xu5"] = stcu_5
    C["S_stoch_xu15"] = stcu_15
    C["S_dc_xu15"] = dcxu_15
    C["S_dc_xu1h"] = dcxu_1h
    # SHORT — trend context
    C["S_sma200dn_D"] = (sma200D > 0) & (close < sma200D)
    C["S_sma200dn_1h"] = (sma200_1h > 0) & (close < sma200_1h)
    return C, close


def build_tradier_conditions(loaded):
    C = {}
    k5 = stack_field(loaded, "stoch_k_5m", 50)
    k15 = stack_field(loaded, "stoch_k_15m", 50)
    mfi5 = stack_field(loaded, "mfi_5m", 50)
    mfi15 = stack_field(loaded, "mfi_15m", 50)
    rsi15 = stack_field(loaded, "rsi_15m", 50)
    rsi5 = stack_field(loaded, "rsi_5m", 50)
    close = stack_field(loaded, "close", 0)
    sma200D = stack_field(loaded, "sma_200_D", 0)
    sma200_1h = stack_field(loaded, "sma_200_1h", 0)
    dc_pos15 = stack_field(loaded, "dc_position_15m", 0.5)

    wtb_5 = stack_bool(loaded, "wt_bullish_5m")
    wtb_15 = stack_bool(loaded, "wt_bullish_15m")
    wtb_1h = stack_bool(loaded, "wt_bullish_1h")
    wtb_4h = stack_bool(loaded, "wt_bullish_4h")
    wtb_D = stack_bool(loaded, "wt_bullish_D")
    wtc_5 = stack_bool(loaded, "wt_cross_bull_5m")
    wtc_15 = stack_bool(loaded, "wt_cross_bull_15m")
    stc_5 = stack_bool(loaded, "stoch_crossover_5m")

    k1h = stack_field(loaded, "stoch_k_1h", 50)
    k4h = stack_field(loaded, "stoch_k_4h", 50)
    rsi1h = stack_field(loaded, "rsi_1h", 50)
    bb_15m = stack_field(loaded, "bb_pct_b_15m", 0.5)
    bb_1h = stack_field(loaded, "bb_pct_b_1h", 0.5)
    atr_15m = stack_field(loaded, "atr_pct_15m", 0)
    sma200_5m = stack_field(loaded, "sma_200_5m", 0)
    dcb_5 = stack_bool(loaded, "dc_basis_crossover_5m")
    dcb_15 = stack_bool(loaded, "dc_basis_crossover_15m")
    dcb_1h = stack_bool(loaded, "dc_basis_crossover_1h")
    dcbu_15 = stack_bool(loaded, "dc_basis_crossunder_15m")
    dcbu_1h = stack_bool(loaded, "dc_basis_crossunder_1h")
    wtc_1h = stack_bool(loaded, "wt_cross_bull_1h")
    wtcr_5 = stack_bool(loaded, "wt_cross_bear_5m")
    wtcr_15 = stack_bool(loaded, "wt_cross_bear_15m")
    stcu_5 = stack_bool(loaded, "stoch_crossunder_5m")
    stcu_15 = stack_bool(loaded, "stoch_crossunder_15m")
    # LONG — stoch threshold sweep (refined 2026-04-17: tighter range around winning cluster k_lt_20-30)
    for tf, ka in [("5", k5), ("15", k15), ("1h", k1h), ("4h", k4h)]:
        for thr in (15, 20, 25, 30, 35, 40, 50, 60):
            C[f"L_k{tf}_lt{thr}"] = ka < thr
    # LONG — MFI threshold sweep
    for tf, ma in [("5", mfi5), ("15", mfi15)]:
        for thr in (20, 25, 30, 35, 40, 50):
            C[f"L_mfi{tf}_lt{thr}"] = ma < thr
    # LONG — RSI threshold sweep (tighter: winning cluster = rsi_lt_25-35)
    for tf, ra in [("5", rsi5), ("15", rsi15), ("1h", rsi1h)]:
        for thr in (20, 22, 25, 28, 30, 32, 35, 38, 40, 45):
            C[f"L_rsi{tf}_lt{thr}"] = ra < thr
    # LONG — DC position
    for thr in (10, 20, 30, 40, 50):
        C[f"L_dcpos_lt{thr}"] = dc_pos15 < (thr / 100.0)
    # LONG — BB lower
    for thr in (0, 10, 20, 30):
        C[f"L_bb15_lt{thr}"] = bb_15m < (thr / 100.0)
        C[f"L_bb1h_lt{thr}"] = bb_1h < (thr / 100.0)
    # LONG — WT bullish flags
    C["L_wt_5m"] = wtb_5
    C["L_wt_15m"] = wtb_15
    C["L_wt_1h"] = wtb_1h
    C["L_wt_4h"] = wtb_4h
    C["L_wt_D"] = wtb_D
    C["L_wt_all3"] = wtb_1h & wtb_4h & wtb_D
    C["L_wt_2of3"] = (wtb_1h.astype(int) + wtb_4h.astype(int) + wtb_D.astype(int)) >= 2
    # LONG — crosses
    C["L_wt_x5"] = wtc_5
    C["L_wt_x15"] = wtc_15
    C["L_wt_x1h"] = wtc_1h
    C["L_stoch_x5"] = stc_5
    C["L_dc_x5"] = dcb_5
    C["L_dc_x15"] = dcb_15
    C["L_dc_x1h"] = dcb_1h
    # LONG — trend
    C["L_sma200up_D"] = (sma200D > 0) & (close > sma200D)
    C["L_sma200up_1h"] = (sma200_1h > 0) & (close > sma200_1h)
    C["L_sma200up_5m"] = (sma200_5m > 0) & (close > sma200_5m)
    C["L_above_sma5pct"] = (sma200D > 0) & ((close - sma200D) / sma200D > 0.05)
    # LONG — momentum-continuation (refined 2026-04-17: +mom_dcpos_gt60, +mom_dcpos_gt80 bracketing winners)
    C["L_mom_mfi_gt50"] = mfi15 > 50
    C["L_mom_mfi_gt60"] = mfi15 > 60
    C["L_mom_dcpos_gt50"] = dc_pos15 > 0.5
    C["L_mom_dcpos_gt60"] = dc_pos15 > 0.6
    C["L_mom_dcpos_gt70"] = dc_pos15 > 0.7
    C["L_mom_dcpos_gt80"] = dc_pos15 > 0.8
    C["L_mom_bb15_gt70"] = bb_15m > 0.7
    C["L_mom_bb15_gt80"] = bb_15m > 0.8
    C["L_atr_gt1"] = atr_15m > 1.0
    # SHORT — stoch upper (tighter around winning cluster k_gt_80-90)
    for tf, ka in [("5", k5), ("15", k15), ("1h", k1h), ("4h", k4h)]:
        for thr in (40, 50, 60, 70, 75, 80, 85, 90):
            C[f"S_k{tf}_gt{thr}"] = ka > thr
    # SHORT — MFI upper
    for tf, ma in [("5", mfi5), ("15", mfi15)]:
        for thr in (50, 60, 65, 70, 75, 80):
            C[f"S_mfi{tf}_gt{thr}"] = ma > thr
    # SHORT — RSI upper (tighter: winning cluster rsi_gt_60-75)
    for tf, ra in [("5", rsi5), ("15", rsi15), ("1h", rsi1h)]:
        for thr in (55, 60, 62, 65, 68, 70, 72, 75, 78, 80):
            C[f"S_rsi{tf}_gt{thr}"] = ra > thr
    # SHORT — DC position upper
    for thr in (50, 60, 70, 75, 80, 85, 90):
        C[f"S_dcpos_gt{thr}"] = dc_pos15 > (thr / 100.0)
    # SHORT — BB upper
    for thr in (70, 75, 80, 85, 90, 95, 100):
        C[f"S_bb15_gt{thr}"] = bb_15m > (thr / 100.0)
        C[f"S_bb1h_gt{thr}"] = bb_1h > (thr / 100.0)
    # SHORT — WT not bullish
    C["S_not_wt_5m"] = ~wtb_5
    C["S_not_wt_15m"] = ~wtb_15
    C["S_not_wt_1h"] = ~wtb_1h
    C["S_not_wt_4h"] = ~wtb_4h
    C["S_not_wt_D"] = ~wtb_D
    C["S_not_wt_all3"] = (~wtb_1h) & (~wtb_4h) & (~wtb_D)
    C["S_not_wt_2of3"] = ((~wtb_1h).astype(int) + (~wtb_4h).astype(int) + (~wtb_D).astype(int)) >= 2
    # SHORT — bear crosses
    C["S_wt_xr5"] = wtcr_5
    C["S_wt_xr15"] = wtcr_15
    C["S_stoch_xu5"] = stcu_5
    C["S_stoch_xu15"] = stcu_15
    C["S_dc_xu15"] = dcbu_15
    C["S_dc_xu1h"] = dcbu_1h
    # SHORT — trend
    C["S_sma200dn_D"] = (sma200D > 0) & (close < sma200D)
    C["S_sma200dn_1h"] = (sma200_1h > 0) & (close < sma200_1h)
    C["S_below_sma5pct"] = (sma200D > 0) & ((close - sma200D) / sma200D < -0.05)
    return C, close


def fwd_returns(close, horizons):
    # fwd[h][t, s] = close[t+h, s] / close[t, s] - 1
    out = {}
    for h in horizons:
        if h >= close.shape[0]:
            continue
        rolled = np.roll(close, -h, axis=0)
        ret = np.where(close > 0, rolled / close - 1, 0.0)
        ret[-h:] = 0.0  # invalidate last h bars
        out[h] = ret
    return out


def score(mask, ret, min_trades):
    rets = ret[mask]
    n = len(rets)
    if n < min_trades:
        return None
    mean = float(rets.mean())
    std = float(rets.std())
    if std <= 0:
        return None
    # Annualize: assume base TF is 15m (crypto) or 5m (tradier), both ~252 trading days
    # For simplicity, use sqrt(N) style — report raw trade-level Sharpe
    sharpe = mean / std
    wins = rets > 0
    wr = float(wins.mean() * 100)
    if n >= 1 and rets[wins].sum() > 0 and (~wins).sum() > 0:
        pf = float(rets[wins].sum() / abs(rets[~wins].sum())) if abs(rets[~wins].sum()) > 0 else 0.0
    else:
        pf = 0.0
    return {"n": n, "mean": mean, "std": std, "sharpe": sharpe, "wr": wr, "pf": pf}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--bars", type=int, default=40000)
    ap.add_argument("--pick", type=int, default=3, help="N conditions to AND together")
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--out", type=str, default="")
    ap.add_argument("--top-report", type=int, default=30)
    from vector_mandatory_coverage import add_coverage_claim_arguments, enforce_coverage_claim
    add_coverage_claim_arguments(ap)
    args = ap.parse_args()
    coverage_contract = enforce_coverage_claim(args, runner="vec_mass_scan.py")
    print(f"V8_VECTOR_GROUND_RULE: {coverage_contract['coverage_status']} shortlist_sha256={coverage_contract['shortlist_sha256']}", flush=True)

    syms = try_symbols(args.mode)
    t0 = time.time()
    loaded, n_bars, npz_dir = load_slice(args.mode, syms, args.bars)
    print(f"[{time.time()-t0:.1f}s] Loaded {len(loaded)} symbols, {n_bars} bars from {npz_dir}")

    if args.mode == "crypto":
        C, close = build_crypto_conditions(loaded)
    else:
        C, close = build_tradier_conditions(loaded)
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} base conditions")

    fwd = fwd_returns(close, HORIZONS)
    print(f"[{time.time()-t0:.1f}s] Computed forward returns for horizons {list(fwd.keys())}")

    long_keys = sorted([k for k in C if k.startswith("L_")])
    short_keys = sorted([k for k in C if k.startswith("S_")])

    def iter_combos(keys, pick):
        for combo in itertools.combinations(keys, pick):
            yield combo

    n_long_combos = sum(1 for _ in iter_combos(long_keys, args.pick))
    n_short_combos = sum(1 for _ in iter_combos(short_keys, args.pick))
    total = (n_long_combos + n_short_combos) * len(fwd)
    print(f"[{time.time()-t0:.1f}s] Long combos: {n_long_combos}, Short: {n_short_combos}, × {len(fwd)} horizons = {total:,} tests")

    results = []
    done = 0
    for side, keys in [("L", long_keys), ("S", short_keys)]:
        for combo in iter_combos(keys, args.pick):
            mask = np.ones_like(C[combo[0]])
            for k in combo:
                mask &= C[k]
            for h, ret in fwd.items():
                m = score(mask, ret, args.min_trades)
                done += 1
                if m is None:
                    continue
                results.append({
                    "side": side,
                    "combo": "+".join(k[2:] for k in combo),
                    "horizon": h,
                    **m,
                })
            if done % 5000 == 0:
                print(f"[{time.time()-t0:.1f}s] {done:,}/{total:,} tests, {len(results)} passed min_trades")

    print(f"[{time.time()-t0:.1f}s] Done: {len(results)} valid configs out of {total:,} tests")

    # Sort by Sharpe desc
    results.sort(key=lambda r: r["sharpe"], reverse=True)

    out_path = Path(args.out) if args.out else Path(__file__).parent / "data" / "sweep_results" / f"vec_mass_{args.mode}_{int(time.time())}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["side", "combo", "horizon", "n_trades", "sharpe", "wr", "mean_ret", "std", "pf"])
        for r in results:
            w.writerow([r["side"], r["combo"], r["horizon"], r["n"], f"{r['sharpe']:.4f}", f"{r['wr']:.2f}", f"{r['mean']:.5f}", f"{r['std']:.5f}", f"{r['pf']:.3f}"])
    print(f"Wrote {len(results)} rows to {out_path}")

    print(f"\nTOP {args.top_report} by Sharpe (n>={args.min_trades}, any horizon):")
    print(f"{'Side':<4} {'Horizon':>7} {'Sharpe':>8} {'WR%':>6} {'Mean%':>7} {'N':>7} {'PF':>6}  Combo")
    for r in results[:args.top_report]:
        print(f"{r['side']:<4} {r['horizon']:>7d} {r['sharpe']:>8.3f} {r['wr']:>6.1f} {r['mean']*100:>7.3f} {r['n']:>7d} {r['pf']:>6.2f}  {r['combo']}")

    # Count configs clearing quality floors
    min_sharpe = 0.05  # raw trade-level Sharpe; annualized ~= sharpe * sqrt(bars/yr)
    wr_floor = 75.0
    clear_all = [r for r in results if r["wr"] >= wr_floor]
    print(f"\n{len(clear_all)} configs with WR>={wr_floor}% (any Sharpe)")
    top_wr = sorted(clear_all, key=lambda r: r["sharpe"], reverse=True)[:args.top_report]
    print(f"\nTOP by Sharpe with WR>={wr_floor}%:")
    for r in top_wr:
        print(f"{r['side']:<4} {r['horizon']:>7d} {r['sharpe']:>8.3f} {r['wr']:>6.1f} {r['mean']*100:>7.3f} {r['n']:>7d} {r['pf']:>6.2f}  {r['combo']}")


if __name__ == "__main__":
    main()
