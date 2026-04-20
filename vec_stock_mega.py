#!/usr/bin/env python3
"""Vectorized 12-sym stock sweep — REALISTIC EXIT SIMULATION.

Rewrite 2026-04-20: fixes the "losers-counted-as-wins" bug. Instead of computing
returns at a fixed forward horizon (which ignores intra-hold drawdowns), each
signal is held until a TECHNICAL EXIT condition fires, mimicking live behavior
per `feedback_no_pct_stops.md` and `feedback_no_fixed_exits_only_delta.md`.

Exit rules (technical only — no % stops):
  LONG exit  = wt_cross_bear_15m (primary) — held max_hold bars as safety floor
  SHORT exit = wt_cross_bull_15m (primary)

For each signal bar t on symbol s:
  1. exit_bar = first t' > t where exit_mask[t', s] True, capped at t + MAX_HOLD
  2. return = close[exit_bar, s] / close[t, s] - 1 (LONG), negated for SHORT
  3. MAE (max adverse excursion) = worst unrealized drawdown during [t, exit_bar]
  4. TUW (time-under-water) = fraction of hold bars where position was negative
  5. realistic_return is the TRUE P/L at technical exit — matches live logic

WR = fraction of CLOSED trades with realistic_return > 0. No peeking at future.

Aggregation per config × exit rule:
  - Per-symbol Sharpe (mean/std of realistic_returns)
  - Per-symbol WR, mean, MAE, TUW
  - sharpe_avg = mean across symbols (honest, keeps negatives)
  - sharpe_pool = (all events pooled, mean/std)
  - sharpe_robust = min(avg, pool) — ranking metric, outlier-resistant
  - Pre-ranking: LONG runs on top-N uptrend syms, SHORT on bottom-N
"""
import argparse
import csv
import itertools
import json
import os
import sqlite3
import sys
import time
from multiprocessing import Pool, cpu_count
from pathlib import Path

import numpy as np

MIX_12 = ["AAPL", "NVDA", "PLTR", "NEM", "XOM", "CAT", "SPY", "QQQ", "GLD", "META", "AMD", "AVGO"]
# One primary max-hold; no horizon loop. 500 bars × 5m = ~2 weeks safety ceiling.
MAX_HOLD_BARS = 500
MIN_TRADES_PER_SYM = 30
MIN_SYMS_PCT = 0.75
SIDE_RANK_ENABLED = True
SIDE_RANK_KEEP = 8

# Exit rule fields: change here to test different exit logic.
LONG_EXIT_FIELD = "wt_cross_bear_15m"
SHORT_EXIT_FIELD = "wt_cross_bull_15m"

BASE = Path(__file__).resolve().parent
for p in (str(BASE), "/home/niels/binance-sandbox", "/Users/niels/Documents/binance"):
    if p not in sys.path:
        sys.path.insert(0, p)


def detect_npz_dir():
    for base in (Path("/home/niels/binance-sandbox"), Path("/Users/niels/Documents/binance")):
        d = base / "backtest_v8" / "indicators"
        if d.exists() and any(d.glob("*.npz")):
            return d
    raise RuntimeError("No NPZ dir found")


def load_slice(symbols, n_bars):
    npz_dir = detect_npz_dir()
    loaded = {}
    min_len = 10**9
    skipped = []
    for s in symbols:
        p = npz_dir / f"{s}.npz"
        if not p.exists():
            skipped.append(s); continue
        z = dict(np.load(str(p), allow_pickle=True))
        loaded[s] = z
        min_len = min(min_len, len(z["close"]))
    if not loaded:
        raise RuntimeError(f"No NPZ loaded for {symbols} from {npz_dir}")
    use = min(n_bars, min_len)
    for s in list(loaded.keys()):
        z = loaded[s]
        for k in list(z.keys()):
            a = np.asarray(z[k])
            if a.ndim == 1 and len(a) >= use:
                z[k] = a[-use:]
        loaded[s] = z
    print(f"Loaded {len(loaded)} syms, {use} bars. Skipped: {skipped}")
    return loaded, use, npz_dir


def stack_field(loaded, field, default=0.0, syms=None):
    if syms is None: syms = list(loaded.keys())
    arrs = []
    for s in syms:
        a = loaded[s].get(field)
        if a is None:
            a = np.full(len(loaded[s]["close"]), default, dtype=np.float64)
        arrs.append(np.asarray(a, dtype=np.float64))
    return np.stack(arrs, axis=1)


def stack_bool(loaded, field, syms=None):
    if syms is None: syms = list(loaded.keys())
    arrs = []
    for s in syms:
        a = loaded[s].get(field)
        if a is None:
            a = np.zeros(len(loaded[s]["close"]), dtype=bool)
        arrs.append(np.asarray(a, dtype=bool))
    return np.stack(arrs, axis=1)


def build_conditions(loaded, syms):
    C = {}
    k5 = stack_field(loaded, "stoch_k_5m", 50, syms)
    k15 = stack_field(loaded, "stoch_k_15m", 50, syms)
    k1h = stack_field(loaded, "stoch_k_1h", 50, syms)
    k4h = stack_field(loaded, "stoch_k_4h", 50, syms)
    mfi5 = stack_field(loaded, "mfi_5m", 50, syms)
    mfi15 = stack_field(loaded, "mfi_15m", 50, syms)
    rsi5 = stack_field(loaded, "rsi_5m", 50, syms)
    rsi15 = stack_field(loaded, "rsi_15m", 50, syms)
    rsi1h = stack_field(loaded, "rsi_1h", 50, syms)
    close = stack_field(loaded, "close", 0, syms)
    sma200D = stack_field(loaded, "sma_200_D", 0, syms)
    sma200_1h = stack_field(loaded, "sma_200_1h", 0, syms)
    dc_pos15 = stack_field(loaded, "dc_position_15m", 0.5, syms)
    dc_pos1h = stack_field(loaded, "dc_position_1h", 0.5, syms)
    bb_15m = stack_field(loaded, "bb_pct_b_15m", 0.5, syms)
    bb_1h = stack_field(loaded, "bb_pct_b_1h", 0.5, syms)
    bb_4h = stack_field(loaded, "bb_pct_b_4h", 0.5, syms)
    atr_15m = stack_field(loaded, "atr_pct_15m", 0, syms)
    wtb_5 = stack_bool(loaded, "wt_bullish_5m", syms)
    wtb_15 = stack_bool(loaded, "wt_bullish_15m", syms)
    wtb_1h = stack_bool(loaded, "wt_bullish_1h", syms)
    wtb_4h = stack_bool(loaded, "wt_bullish_4h", syms)
    wtb_D = stack_bool(loaded, "wt_bullish_D", syms)
    wtc_5 = stack_bool(loaded, "wt_cross_bull_5m", syms)
    wtc_15 = stack_bool(loaded, "wt_cross_bull_15m", syms)
    wtc_1h = stack_bool(loaded, "wt_cross_bull_1h", syms)
    stc_5 = stack_bool(loaded, "stoch_crossover_5m", syms)
    stc_15 = stack_bool(loaded, "stoch_crossover_15m", syms)
    stc_1h = stack_bool(loaded, "stoch_crossover_1h", syms)
    dcb_5 = stack_bool(loaded, "dc_basis_crossover_5m", syms)
    dcb_15 = stack_bool(loaded, "dc_basis_crossover_15m", syms)
    dcb_1h = stack_bool(loaded, "dc_basis_crossover_1h", syms)
    dcbu_15 = stack_bool(loaded, "dc_basis_crossunder_15m", syms)
    dcbu_1h = stack_bool(loaded, "dc_basis_crossunder_1h", syms)
    wtcr_5 = stack_bool(loaded, "wt_cross_bear_5m", syms)
    wtcr_15 = stack_bool(loaded, "wt_cross_bear_15m", syms)
    stcu_5 = stack_bool(loaded, "stoch_crossunder_5m", syms)
    stcu_15 = stack_bool(loaded, "stoch_crossunder_15m", syms)

    for tf, ka in [("5", k5), ("15", k15), ("1h", k1h), ("4h", k4h)]:
        for thr in (20, 30, 40, 50, 60):
            C[f"L_k{tf}_lt{thr}"] = ka < thr
    for tf, ma in [("5", mfi5), ("15", mfi15)]:
        for thr in (25, 35, 45):
            C[f"L_mfi{tf}_lt{thr}"] = ma < thr
    for tf, ra in [("5", rsi5), ("15", rsi15), ("1h", rsi1h)]:
        for thr in (25, 30, 35, 40, 45):
            C[f"L_rsi{tf}_lt{thr}"] = ra < thr
    for thr in (20, 30, 40, 50):
        C[f"L_dcpos15_lt{thr}"] = dc_pos15 < (thr / 100.0)
        C[f"L_dcpos1h_lt{thr}"] = dc_pos1h < (thr / 100.0)
    for thr in (10, 20, 30):
        C[f"L_bb15_lt{thr}"] = bb_15m < (thr / 100.0)
        C[f"L_bb1h_lt{thr}"] = bb_1h < (thr / 100.0)
        C[f"L_bb4h_lt{thr}"] = bb_4h < (thr / 100.0)
    C["L_wt_5m"] = wtb_5; C["L_wt_15m"] = wtb_15; C["L_wt_1h"] = wtb_1h
    C["L_wt_4h"] = wtb_4h; C["L_wt_D"] = wtb_D
    C["L_wt_all3"] = wtb_1h & wtb_4h & wtb_D
    C["L_wt_2of3"] = (wtb_1h.astype(int) + wtb_4h.astype(int) + wtb_D.astype(int)) >= 2
    C["L_wt_ltf"] = wtb_5 & wtb_15
    C["L_wtx_5m"] = wtc_5; C["L_wtx_15m"] = wtc_15; C["L_wtx_1h"] = wtc_1h
    C["L_stx_5m"] = stc_5; C["L_stx_15m"] = stc_15; C["L_stx_1h"] = stc_1h
    C["L_dcx_5m"] = dcb_5; C["L_dcx_15m"] = dcb_15; C["L_dcx_1h"] = dcb_1h
    C["L_sma200up_D"] = (sma200D > 0) & (close > sma200D)
    C["L_sma200up_1h"] = (sma200_1h > 0) & (close > sma200_1h)
    C["L_above_sma5pct"] = (sma200D > 0) & ((close - sma200D) / np.maximum(sma200D, 1e-9) > 0.05)
    C["L_mom_mfi15_gt50"] = mfi15 > 50
    C["L_mom_mfi15_gt60"] = mfi15 > 60
    C["L_mom_dcpos_gt60"] = dc_pos15 > 0.6
    C["L_mom_dcpos_gt70"] = dc_pos15 > 0.7
    C["L_mom_bb15_gt70"] = bb_15m > 0.7
    C["L_atr_gt1"] = atr_15m > 1.0

    for tf, ka in [("5", k5), ("15", k15), ("1h", k1h), ("4h", k4h)]:
        for thr in (50, 60, 70, 80, 85):
            C[f"S_k{tf}_gt{thr}"] = ka > thr
    for tf, ma in [("5", mfi5), ("15", mfi15)]:
        for thr in (55, 65, 75):
            C[f"S_mfi{tf}_gt{thr}"] = ma > thr
    for tf, ra in [("5", rsi5), ("15", rsi15), ("1h", rsi1h)]:
        for thr in (55, 60, 65, 70, 75):
            C[f"S_rsi{tf}_gt{thr}"] = ra > thr
    for thr in (50, 60, 70, 80):
        C[f"S_dcpos15_gt{thr}"] = dc_pos15 > (thr / 100.0)
        C[f"S_dcpos1h_gt{thr}"] = dc_pos1h > (thr / 100.0)
    for thr in (70, 80, 90):
        C[f"S_bb15_gt{thr}"] = bb_15m > (thr / 100.0)
        C[f"S_bb1h_gt{thr}"] = bb_1h > (thr / 100.0)
        C[f"S_bb4h_gt{thr}"] = bb_4h > (thr / 100.0)
    C["S_not_wt_5m"] = ~wtb_5; C["S_not_wt_15m"] = ~wtb_15
    C["S_not_wt_1h"] = ~wtb_1h; C["S_not_wt_4h"] = ~wtb_4h; C["S_not_wt_D"] = ~wtb_D
    C["S_not_wt_all3"] = (~wtb_1h) & (~wtb_4h) & (~wtb_D)
    C["S_not_wt_2of3"] = ((~wtb_1h).astype(int) + (~wtb_4h).astype(int) + (~wtb_D).astype(int)) >= 2
    C["S_wtxr_5m"] = wtcr_5; C["S_wtxr_15m"] = wtcr_15
    C["S_stxu_5m"] = stcu_5; C["S_stxu_15m"] = stcu_15
    C["S_dcxu_15m"] = dcbu_15; C["S_dcxu_1h"] = dcbu_1h
    C["S_sma200dn_D"] = (sma200D > 0) & (close < sma200D)
    C["S_sma200dn_1h"] = (sma200_1h > 0) & (close < sma200_1h)
    C["S_below_sma5pct"] = (sma200D > 0) & ((close - sma200D) / np.maximum(sma200D, 1e-9) < -0.05)
    return C, close


def precompute_exit_indices(exit_mask, max_hold):
    """For each (t, s), find first bar > t where exit_mask True, cap at t+max_hold, at most n_bars-1.
    Reverse-scan O(n_bars × n_syms). Returns int32 array shape (bars, syms).
    """
    n_bars, n_syms = exit_mask.shape
    exit_at = np.empty((n_bars, n_syms), dtype=np.int32)
    # nxt[s] = first bar >= current_t+1 where exit_mask True, or n_bars (sentinel)
    nxt = np.full(n_syms, n_bars, dtype=np.int32)
    for t in range(n_bars - 1, -1, -1):
        # Exit from signal at t: min(nxt, t+max_hold), clipped to valid range
        cand = np.minimum(nxt, t + max_hold)
        exit_at[t] = np.minimum(cand, n_bars - 1)
        # Before next iteration, update nxt to consider bar t
        nxt = np.where(exit_mask[t], t, nxt)
    return exit_at


def score_realistic(entry_mask, close, exit_idx, side, sym_select=None):
    """Compute returns at technical exit, not forward horizon.
    entry_mask: (bars, syms) bool
    close: (bars, syms) float
    exit_idx: (bars, syms) int — bar index where exit fires (from precompute_exit_indices)
    side: "L" or "S"
    sym_select: list of column indices for long/short candidates
    Returns dict with honest metrics or None if insufficient.
    """
    n_bars, n_syms = close.shape
    sym_range = list(sym_select) if sym_select is not None else list(range(n_syms))
    per_sym_sharpe = []; per_sym_wr = []; per_sym_mean = []
    per_sym_mae = []; per_sym_tuw = []
    n_trades_total = 0
    pooled_rets = []

    for s in sym_range:
        signal_bars = np.where(entry_mask[:, s])[0]
        if len(signal_bars) < MIN_TRADES_PER_SYM:
            continue
        exit_bars = exit_idx[signal_bars, s]
        entry_price = close[signal_bars, s]
        exit_price = close[exit_bars, s]
        # Guard against zero prices (should not happen but defensive)
        valid = (entry_price > 0) & (exit_price > 0) & (exit_bars > signal_bars)
        if valid.sum() < MIN_TRADES_PER_SYM:
            continue
        signal_bars = signal_bars[valid]
        exit_bars = exit_bars[valid]
        entry_price = entry_price[valid]
        exit_price = exit_price[valid]

        # Realistic return (LONG: exit/entry - 1; SHORT: negated)
        raw_ret = exit_price / entry_price - 1.0
        if side == "S":
            raw_ret = -raw_ret

        # MAE & TUW — per-signal scan of close within hold window [t, exit_bar]
        # Vectorized: at most max-hold long windows. Loop per signal but window size is small.
        mae_list = np.zeros(len(signal_bars))
        tuw_list = np.zeros(len(signal_bars))
        col = close[:, s]
        for i, (t0, t1) in enumerate(zip(signal_bars, exit_bars)):
            window = col[t0:t1 + 1]
            if len(window) < 2:
                continue
            ep = entry_price[i]
            if side == "L":
                # worst drop below entry
                mae_list[i] = float(window.min() / ep - 1.0)
                tuw_list[i] = float((window < ep).mean())
            else:
                # SHORT: worst rise above entry (we lose when price goes up)
                mae_list[i] = float(-(window.max() / ep - 1.0))
                tuw_list[i] = float((window > ep).mean())

        std = float(raw_ret.std())
        if std <= 0:
            continue
        mean = float(raw_ret.mean())
        sh = mean / std
        per_sym_sharpe.append(sh)
        per_sym_mean.append(mean)
        per_sym_wr.append(float((raw_ret > 0).mean() * 100))
        per_sym_mae.append(float(np.mean(mae_list)))
        per_sym_tuw.append(float(np.mean(tuw_list)))
        n_trades_total += len(signal_bars)
        pooled_rets.append(raw_ret)

    total_eligible = len(sym_range)
    min_included = max(int(total_eligible * MIN_SYMS_PCT), 3)
    if len(per_sym_sharpe) < min_included:
        return None
    arr = np.asarray(per_sym_sharpe)
    pool = np.concatenate(pooled_rets)
    if pool.size == 0 or pool.std() <= 0:
        return None
    pool_sh = float(pool.mean() / pool.std())
    sh_avg = float(arr.mean())
    return {
        "sharpe_avg": sh_avg,
        "sharpe_pool": pool_sh,
        "sharpe_robust": min(sh_avg, pool_sh),
        "sharpe_min": float(arr.min()),
        "sharpe_p25": float(np.percentile(arr, 25)) if len(arr) >= 4 else float(arr.min()),
        "sharpe_med": float(np.median(arr)),
        "sharpe_p75": float(np.percentile(arr, 75)) if len(arr) >= 4 else float(arr.max()),
        "sharpe_max": float(arr.max()),
        "syms_included": len(per_sym_sharpe),
        "n_trades_total": n_trades_total,
        "wr_avg": float(np.mean(per_sym_wr)),
        "mean_ret_avg": float(np.mean(per_sym_mean)),
        "mae_avg": float(np.mean(per_sym_mae)),
        "tuw_avg": float(np.mean(per_sym_tuw)),
    }


def rank_syms_by_uptrend(loaded, syms):
    scores = []
    for i, s in enumerate(syms):
        close = np.asarray(loaded[s].get("close"), dtype=np.float64)
        if close is None or len(close) < 2 or close[0] <= 0 or close[-1] <= 0:
            scores.append((i, 0.0)); continue
        scores.append((i, float(np.log(close[-1] / close[0]))))
    scores.sort(key=lambda x: x[1], reverse=True)
    keep = min(SIDE_RANK_KEEP, len(syms))
    long_idx = sorted(idx for idx, _ in scores[:keep])
    short_idx = sorted(idx for idx, _ in scores[-keep:])
    return long_idx, short_idx, scores


# Worker-shared
_C = None; _CLOSE = None; _EXIT_IDX_L = None; _EXIT_IDX_S = None
_KEYS_L = None; _KEYS_S = None; _LONG_SYMS = None; _SHORT_SYMS = None


def init_worker(state_path):
    global _C, _CLOSE, _EXIT_IDX_L, _EXIT_IDX_S, _KEYS_L, _KEYS_S, _LONG_SYMS, _SHORT_SYMS
    st = np.load(state_path, allow_pickle=True).item()
    _C = st["C"]; _CLOSE = st["CLOSE"]
    _EXIT_IDX_L = st["EXIT_IDX_L"]; _EXIT_IDX_S = st["EXIT_IDX_S"]
    _KEYS_L = st["KEYS_L"]; _KEYS_S = st["KEYS_S"]
    _LONG_SYMS = st.get("LONG_SYMS"); _SHORT_SYMS = st.get("SHORT_SYMS")


def run_chunk(args):
    side, combos = args
    results = []
    keys = _KEYS_L if side == "L" else _KEYS_S
    sym_sel = _LONG_SYMS if side == "L" else _SHORT_SYMS
    exit_idx = _EXIT_IDX_L if side == "L" else _EXIT_IDX_S
    for combo in combos:
        names = tuple(keys[i] for i in combo)
        mask = _C[names[0]].copy()
        for n in names[1:]:
            mask &= _C[n]
        if not mask.any():
            continue
        m = score_realistic(mask, _CLOSE, exit_idx, side, sym_select=sym_sel)
        if m is None:
            continue
        results.append({"side": side, "combo": "+".join(n[2:] for n in names), **m})
    return results


def chunker(iterable, size):
    buf = []
    for x in iterable:
        buf.append(x)
        if len(buf) >= size:
            yield buf; buf = []
    if buf:
        yield buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=40000, help="~2yr of 5m stock bars")
    ap.add_argument("--pick", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=200)
    ap.add_argument("--floor-robust", type=float, default=0.05, help="save only configs with sharpe_robust >= floor")
    ap.add_argument("--elite-robust", type=float, default=1.0)
    ap.add_argument("--out-db", type=str, default="")
    ap.add_argument("--time-budget-s", type=int, default=3600)
    ap.add_argument("--sector", type=str, default="")
    ap.add_argument("--syms", type=str, default="")
    ap.add_argument("--sectors-json", type=str, default="/home/niels/binance-sandbox/stocks_sectors.json")
    ap.add_argument("--max-hold", type=int, default=MAX_HOLD_BARS)
    args = ap.parse_args()

    syms = [s for s in MIX_12]
    if args.syms:
        syms = [s.strip() for s in args.syms.split(",") if s.strip()]
    elif args.sector:
        try:
            with open(args.sectors_json) as f:
                secmap = json.load(f)
            syms = [s for s in secmap[args.sector] if isinstance(s, str)]
        except FileNotFoundError:
            with open("/Users/niels/Documents/binance/stocks_sectors.json") as f:
                secmap = json.load(f)
            syms = [s for s in secmap[args.sector] if isinstance(s, str)]

    print(f"Symbols ({len(syms)}): {syms}")
    t0 = time.time()
    loaded, n_bars, npz_dir = load_slice(syms, args.bars)
    syms_used = list(loaded.keys())
    print(f"[{time.time()-t0:.1f}s] Loaded {len(syms_used)} syms × {n_bars} bars")

    C, close = build_conditions(loaded, syms_used)
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} conditions")

    # Exit-bar precomputation per side
    long_exit_mask = stack_bool(loaded, LONG_EXIT_FIELD, syms_used)
    short_exit_mask = stack_bool(loaded, SHORT_EXIT_FIELD, syms_used)
    exit_idx_L = precompute_exit_indices(long_exit_mask, args.max_hold)
    exit_idx_S = precompute_exit_indices(short_exit_mask, args.max_hold)
    # Sanity: how many signals have technical exit within max_hold vs forced to max_hold?
    forced_L = np.mean(exit_idx_L - np.arange(n_bars).reshape(-1, 1) >= args.max_hold)
    forced_S = np.mean(exit_idx_S - np.arange(n_bars).reshape(-1, 1) >= args.max_hold)
    print(f"[{time.time()-t0:.1f}s] Exit rules: LONG={LONG_EXIT_FIELD} (forced @max_hold: {forced_L*100:.1f}%), SHORT={SHORT_EXIT_FIELD} (forced: {forced_S*100:.1f}%)")

    if SIDE_RANK_ENABLED and len(syms_used) > SIDE_RANK_KEEP:
        long_syms, short_syms, _ = rank_syms_by_uptrend(loaded, syms_used)
        print(f"[{time.time()-t0:.1f}s] Uptrend ranking ({SIDE_RANK_KEEP}/{len(syms_used)}):")
        print(f"  LONG candidates:  {[syms_used[i] for i in long_syms]}")
        print(f"  SHORT candidates: {[syms_used[i] for i in short_syms]}")
    else:
        long_syms = short_syms = None

    state_path = Path(f"/tmp/vec_stock_mega_state_{os.getpid()}.npy")
    np.save(str(state_path), {
        "C": C, "CLOSE": close,
        "EXIT_IDX_L": exit_idx_L, "EXIT_IDX_S": exit_idx_S,
        "KEYS_L": sorted([k for k in C if k.startswith("L_")]),
        "KEYS_S": sorted([k for k in C if k.startswith("S_")]),
        "LONG_SYMS": long_syms, "SHORT_SYMS": short_syms,
    })
    keys_L = sorted([k for k in C if k.startswith("L_")])
    keys_S = sorted([k for k in C if k.startswith("S_")])
    n_L = sum(1 for _ in itertools.combinations(range(len(keys_L)), args.pick))
    n_S = sum(1 for _ in itertools.combinations(range(len(keys_S)), args.pick))
    total = n_L + n_S
    print(f"[{time.time()-t0:.1f}s] Combos: L={n_L:,} S={n_S:,} total={total:,}")

    out_db = Path(args.out_db) if args.out_db else BASE / "data" / "sweep_results" / f"vec_stock_mega_{int(time.time())}.sqlite"
    out_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(out_db))
    conn.execute("""CREATE TABLE IF NOT EXISTS results (
        side TEXT, combo TEXT,
        sharpe_avg REAL, sharpe_pool REAL, sharpe_robust REAL,
        sharpe_min REAL, sharpe_p25 REAL, sharpe_med REAL, sharpe_p75 REAL, sharpe_max REAL,
        syms_included INTEGER, n_trades_total INTEGER,
        wr_avg REAL, mean_ret_avg REAL, mae_avg REAL, tuw_avg REAL,
        is_elite INTEGER DEFAULT 0)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_robust ON results(sharpe_robust DESC)")

    def make_tasks(side, keys):
        idxs = list(range(len(keys)))
        for chunk in chunker(itertools.combinations(idxs, args.pick), args.chunk):
            yield (side, chunk)

    kept = 0; elite = 0; done_combos = 0
    report_t = time.time()
    with Pool(args.workers, initializer=init_worker, initargs=(str(state_path),)) as pool:
        tasks = itertools.chain(make_tasks("L", keys_L), make_tasks("S", keys_S))
        for batch in pool.imap_unordered(run_chunk, tasks, chunksize=1):
            done_combos += args.chunk  # approx
            for r in batch:
                if r["sharpe_robust"] >= args.floor_robust:
                    is_elite = 1 if r["sharpe_robust"] >= args.elite_robust else 0
                    conn.execute("""INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        r["side"], r["combo"],
                        r["sharpe_avg"], r["sharpe_pool"], r["sharpe_robust"],
                        r["sharpe_min"], r["sharpe_p25"], r["sharpe_med"], r["sharpe_p75"], r["sharpe_max"],
                        r["syms_included"], r["n_trades_total"],
                        r["wr_avg"], r["mean_ret_avg"], r["mae_avg"], r["tuw_avg"],
                        is_elite,
                    ))
                    kept += 1
                    if is_elite:
                        elite += 1
            if time.time() - report_t > 20:
                conn.commit()
                elapsed = time.time() - t0
                rate = done_combos / max(1, elapsed)
                top = conn.execute("""SELECT side, combo, sharpe_robust, sharpe_avg, sharpe_pool, wr_avg, mean_ret_avg, mae_avg, tuw_avg, n_trades_total, syms_included
                    FROM results ORDER BY sharpe_robust DESC LIMIT 5""").fetchall()
                print(f"[{elapsed:6.0f}s] done~{done_combos}/{total} kept={kept} elite={elite} rate={rate:,.0f}c/s  top5:")
                for row in top:
                    print(f"    {row[0]} rob={row[2]:.3f} avg={row[3]:.3f} pool={row[4]:.3f} WR={row[5]:.1f}% mean={row[6]*100:.2f}% MAE={row[7]*100:.2f}% TUW={row[8]*100:.0f}% n={row[9]} syms={row[10]} {row[1][:65]}")
                report_t = time.time()
                if elapsed > args.time_budget_s:
                    print(f"[{elapsed:.0f}s] HARD STOP — time budget reached"); pool.terminate(); break
    conn.commit()

    elapsed = time.time() - t0
    n_rows = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    print(f"\n=== DONE [{elapsed:.0f}s] total kept={n_rows}, elite={elite}, db={out_db} ===\n")
    top_report = conn.execute("""SELECT side, combo, sharpe_robust, sharpe_avg, sharpe_pool, sharpe_min, sharpe_max,
        wr_avg, mean_ret_avg, mae_avg, tuw_avg, n_trades_total, syms_included
        FROM results ORDER BY sharpe_robust DESC LIMIT 30""").fetchall()
    print("TOP 30 by sharpe_robust (REAL technical exit — wins are realized P/L, not fwd-horizon):")
    print(f"{'Sd':<2} {'Rob':>6} {'Avg':>6} {'Pool':>6} {'Min':>6} {'Max':>6} {'WR%':>5} {'Mean%':>6} {'MAE%':>6} {'TUW%':>5} {'N':>7} {'sy':>3}  Combo")
    for row in top_report:
        side, combo, rob, sh, pool, mi, mx, wr, mr, mae, tuw, n, ns = row
        print(f"{side:<2} {rob:>6.3f} {sh:>6.3f} {pool:>6.3f} {mi:>6.3f} {mx:>6.3f} {wr:>5.1f} {mr*100:>6.2f} {mae*100:>6.2f} {tuw*100:>4.0f}% {n:>7d} {ns:>3d}  {combo[:70]}")
    conn.close()
    try: state_path.unlink()
    except Exception: pass


if __name__ == "__main__":
    main()
