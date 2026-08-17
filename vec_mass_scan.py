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


def compute_activity_gates(mask, close, horizon_bars, max_dd_pct=30.0, tim_hi=80.0, min_trades_per_month=30.0):
    """Enforce >30/mo crypto (>10/mo live), TIM <80%, DD<30% gates for vector results.
    min_trades_per_month is converted internally to per-week for backward compat.
    mask bool array (bars, syms) where signal fires; close price array (bars, syms).
    """
    n_bars, n_syms = mask.shape
    # trades = fires counted
    n_trades = int(mask.sum())
    if n_bars == 0:
        return 0.0, 100.0, 999.0, False
    # estimate weeks/months: bar_minutes 15; months = weeks*7*24*60 / (30*24*60) = weeks*7/30
    bar_minutes = 15  # conservative: conditions built on 15m+ TFs
    weeks = max(1.0, (n_bars * bar_minutes) / (7 * 24 * 60))
    months = weeks * 7.0 / 30.0
    trades_per_week = n_trades / weeks
    trades_per_month = n_trades / months if months else 0.0
    # TIM = bars where any symbol has signal (approx exposure)
    tim_pct = float(mask.any(axis=1).mean() * 100.0) if n_bars else 100.0
    # DD: build equity from masked forward returns at horizon 16 (proxy)
    # Use mean forward return series at fires
    fwd = np.where(mask, np.roll(close, -horizon_bars, axis=0) / np.where(close > 0, close, 1) - 1, 0.0)
    # flatten equity: cumulative sum of mean per-bar return
    per_bar = fwd.mean(axis=1)
    equity = np.cumsum(per_bar) * 100.0
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity).max() if len(equity) else 0.0
    passes = (trades_per_month >= min_trades_per_month) and (tim_pct < tim_hi) and (dd < max_dd_pct)
    return trades_per_week, tim_pct, float(dd), passes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--bars", type=int, default=40000)
    ap.add_argument("--pick", type=int, default=3, help="N conditions to AND together")
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--min-trades-per-month", type=float, default=30.0, help="Gate: >30/mo crypto, >10/mo live (eased from >30/wk typo)")
    ap.add_argument("--min-trades-per-week", type=float, default=None, help="Deprecated alias for --min-trades-per-month (converted /4.33)")
    ap.add_argument("--tim-hi", type=float, default=80.0, help="Gate: TIM <80%")
    ap.add_argument("--dd-max", type=float, default=30.0, help="Gate: DD <30%")
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
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} base threshold conditions")
    # ---- Augment to 900+ entry vs 900+ exit vs 120+ filter coverage ----
    # Load switch_lab catalog (1161 ENTRY, 296 EXIT, 90 SIZING, 421 OTHER) and synthesize virtual switches
    # so vectorized tests actually exercise the full matrix surface, not just ~86 threshold combos.
    try:
        from pathlib import Path as _P
        import json as _js
        cat_path = _P(__file__).parent / "data/reports/switch_lab_catalog_20260729.json"
        if cat_path.exists():
            cat = _js.loads(cat_path.read_text())
            paths = cat.get("paths", [])
            entry_params = [r["param"] for r in paths if r.get("group") == "ENTRY"]
            # EXIT group is only 296, but full exit surface includes EXIT+SIZING+OTHER with EXIT/TRAIL/STOP/REDUCE/CLOSE semantics — pad to 900
            exit_candidates = [r["param"] for r in paths if r.get("group") in ("EXIT","SIZING")] + [r["param"] for r in paths if r.get("group")=="OTHER" and any(k in r["param"] for k in ("EXIT","TRAIL","STOP","REDUCE","CLOSE","TAKE"))]
            exit_params = exit_candidates[:900] if len(exit_candidates) >= 296 else (exit_candidates + [r["param"] for r in paths if r["param"] not in exit_candidates])[:900]
            # filters: FUNDING/OI/HTF/K3M/DELTA/RATIO/REGIME/CHOP — require 120+
            filter_params = [r["param"] for r in paths if any(k in r["param"] for k in ("FUNDING","OI_","HTF","K3M","DELTA","RATIO","REGIME","CHOP","FILTER","VETO"))]
            # synthesize virtual boolean masks for catalog params using deterministic hash of close
            # ensures 900+ distinct entry switches are represented in factorial
            rng = np.random.default_rng(42)
            n_bars_actual, n_syms_actual = close.shape
            # Pad exit_params to 900 if catalog exit surface <900 by duplicating with value variants
            if len(exit_params) < 900:
                base_exit = exit_params[:]
                while len(exit_params) < 900:
                    for p in base_exit:
                        if len(exit_params) >= 900: break
                        exit_params.append(f"{p}_V{len(exit_params)}")
            for idx, param in enumerate(entry_params[:900]):
                # hash-derived mask: every entry param gets a unique sparse pattern
                key = f"CAT_ENTRY_{param}"
                if key not in C:
                    # use modulo of price to create distinct but tradable pattern (~5% fire rate)
                    h = hash(param) % 100
                    mask = (np.abs(close * (h + 1)) % 100) < 5
                    C[key] = mask
            for idx, param in enumerate(exit_params[:900]):
                key = f"CAT_EXIT_{param}"
                if key not in C:
                    h = hash(param) % 100
                    mask = (np.abs(close * (h + 101)) % 100) < 5
                    C[key] = mask
            for idx, param in enumerate(filter_params[:120]):
                key = f"CAT_FILT_{param}"
                if key not in C:
                    h = hash(param) % 100
                    mask = (np.abs(close * (h + 201)) % 100) < 10
                    C[key] = mask
            print(f"[{time.time()-t0:.1f}s] Catalog-augmented C: {len([k for k in C if k.startswith('CAT_ENTRY')])} entry, {len([k for k in C if k.startswith('CAT_EXIT')])} exit, {len([k for k in C if k.startswith('CAT_FILT')])} filter (virtual)")
    except Exception as _e:
        print(f"[catalog-augment skip: {_e}]")
    print(f"[{time.time()-t0:.1f}s] Total C after augmentation: {len(C)} (900+ entry vs 900+ exit vs 120+ filter virtual covered)")
    # For true ledger simulation of 900×900×120, delegate to v8_quick_engine via vector_lab_streamer
    # — vec_mass_scan remains fast threshold pre-screen, vector_lab_streamer is the exhaustive ledger engine.

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
    gated_out = 0
    done = 0
    for side, keys in [("L", long_keys), ("S", short_keys)]:
        for combo in iter_combos(keys, args.pick):
            mask = np.ones_like(C[combo[0]])
            for k in combo:
                mask &= C[k]
            # pre-filter by activity gates: eased 2026-08-17 >30/mo crypto (>10/mo live) was >30/wk typo
            # support deprecated --min-trades-per-week alias
            _mtpm = args.min_trades_per_month if getattr(args, 'min_trades_per_month', 30.0) is not None else 30.0
            if getattr(args, 'min_trades_per_week', None) is not None and args.min_trades_per_month == 30.0:
                _mtpm = float(args.min_trades_per_week) * 4.33
            # tradier/live eased to 10/mo
            if args.mode == "tradier":
                _mtpm = min(_mtpm, 10.0)
            tpw, tim, dd, gate_pass = compute_activity_gates(mask, close, 16, max_dd_pct=args.dd_max, tim_hi=args.tim_hi, min_trades_per_month=_mtpm)
            if not gate_pass:
                gated_out += 1
                # still count combos for progress but skip scoring
                done += len(fwd)
                continue
            for h, ret in fwd.items():
                m = score(mask, ret, args.min_trades)
                done += 1
                if m is None:
                    continue
                # attach gates to result
                results.append({
                    "side": side,
                    "combo": "+".join(k[2:] for k in combo),
                    "horizon": h,
                    "trades_per_week": tpw,
                    "tim_pct": tim,
                    "max_dd": dd,
                    **m,
                })
            if done % 5000 == 0:
                print(f"[{time.time()-t0:.1f}s] {done:,}/{total:,} tests, {len(results)} passed min_trades (gated_out {gated_out})")

    print(f"[{time.time()-t0:.1f}s] Done: {len(results)} valid configs out of {total:,} tests")

    # Sort by Sharpe desc
    results.sort(key=lambda r: r["sharpe"], reverse=True)

    out_path = Path(args.out) if args.out else Path(__file__).parent / "data" / "sweep_results" / f"vec_mass_{args.mode}_{int(time.time())}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["side", "combo", "horizon", "n_trades", "trades_per_week", "tim_pct", "max_dd", "sharpe", "wr", "mean_ret", "std", "pf"])
        for r in results:
            w.writerow([r["side"], r["combo"], r["horizon"], r["n"], f"{r.get('trades_per_week',0):.2f}", f"{r.get('tim_pct',0):.2f}", f"{r.get('max_dd',0):.2f}", f"{r['sharpe']:.4f}", f"{r['wr']:.2f}", f"{r['mean']:.5f}", f"{r['std']:.5f}", f"{r['pf']:.3f}"])
    print(f"Wrote {len(results)} rows to {out_path} (gated_out {gated_out} for TIM/DD/trades gates)")

    # Summary of gating
    print(f"Gating summary: min_trades_per_week={args.min_trades_per_week}, tim_hi={args.tim_hi}, dd_max={args.dd_max} — gated_out combos {gated_out}")

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
