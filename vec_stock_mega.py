#!/usr/bin/env python3
"""Vectorized 12-sym stock mega sweep.

Target: ~1M config tests/hour. Per-symbol Sharpe averaging (per CLAUDE.md rule).
Reuses vec_mass_scan build_tradier_conditions. SQLite persistence.
Multi-worker via combo-chunk fanout.

Scoring per config × horizon:
  - Build mask from AND of picked conditions (shape: bars x n_syms)
  - Forward returns at horizon h: ret[t,s] = close[t+h,s]/close[t,s]-1
  - Per symbol: take rets where mask[t,s] True → mean/std → sharpe_s
  - Aggregate: mean Sharpe across syms w/ >= MIN_TRADES_PER_SYM, count included
  - Also pooled Sharpe (all events across syms) for reference
  - Equity curve: per-sym cumsum of mask-selected returns → max drawdown % → avg across syms

Output row: sharpe_avg, sharpe_pool, sharpe_min, sharpe_p25, sharpe_med, sharpe_p75, sharpe_max,
           syms_included, dd_pct_avg, dd_pct_max, n_trades_total, wr_avg, mean_ret_avg, combo, horizon, side
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
HORIZONS = [8, 16, 32, 64, 128, 256]
# Honest averaging: low trade floor (5 = noise but usable), most syms must trigger, NO exclusion of negatives.
MIN_TRADES_PER_SYM = 30           # CLAUDE.md: <30 = statistical noise, not strategy signal
MIN_SYMS_PCT = 0.75               # fraction of SELECTED candidate syms that must trigger (post pre-ranking)
REQUIRE_POSITIVE_MIN_SYM = False  # user 2026-04-20: do NOT drop configs for negative worst-sym
MIN_POOL_SHARPE = 0.0             # save anything — honest floor
# Pre-ranking: LONG runs on top-ranked uptrend syms, SHORT on bottom-ranked — but within each side the avg is across ALL selected (no post-hoc drops).
SIDE_RANK_ENABLED = True
SIDE_RANK_KEEP = 8                # of the 12, keep this many for each side (top-N for LONG, bottom-N for SHORT)

BASE = Path(__file__).resolve().parent
SYS_PATHS = [str(BASE), "/home/niels/binance-sandbox", "/Users/niels/Documents/binance"]
for p in SYS_PATHS:
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
            skipped.append(s)
            continue
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
    if syms is None:
        syms = list(loaded.keys())
    arrs = []
    for s in syms:
        a = loaded[s].get(field)
        if a is None:
            n = len(loaded[s]["close"])
            a = np.full(n, default, dtype=np.float64)
        a = np.asarray(a, dtype=np.float64)
        arrs.append(a)
    return np.stack(arrs, axis=1)


def stack_bool(loaded, field, syms=None):
    if syms is None:
        syms = list(loaded.keys())
    arrs = []
    for s in syms:
        a = loaded[s].get(field)
        if a is None:
            n = len(loaded[s]["close"])
            a = np.zeros(n, dtype=bool)
        arrs.append(np.asarray(a, dtype=bool))
    return np.stack(arrs, axis=1)


def build_conditions(loaded, syms):
    """Stock condition bank. Adapted from vec_mass_scan.build_tradier_conditions."""
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
    wtcr_5 = stack_bool(loaded, "wt_cross_bear_5m", syms)
    wtcr_15 = stack_bool(loaded, "wt_cross_bear_15m", syms)
    stc_5 = stack_bool(loaded, "stoch_crossover_5m", syms)
    stc_15 = stack_bool(loaded, "stoch_crossover_15m", syms)
    stc_1h = stack_bool(loaded, "stoch_crossover_1h", syms)
    stcu_5 = stack_bool(loaded, "stoch_crossunder_5m", syms)
    stcu_15 = stack_bool(loaded, "stoch_crossunder_15m", syms)
    dcb_5 = stack_bool(loaded, "dc_basis_crossover_5m", syms)
    dcb_15 = stack_bool(loaded, "dc_basis_crossover_15m", syms)
    dcb_1h = stack_bool(loaded, "dc_basis_crossover_1h", syms)
    dcbu_15 = stack_bool(loaded, "dc_basis_crossunder_15m", syms)
    dcbu_1h = stack_bool(loaded, "dc_basis_crossunder_1h", syms)

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
    C["L_wt_5m"] = wtb_5
    C["L_wt_15m"] = wtb_15
    C["L_wt_1h"] = wtb_1h
    C["L_wt_4h"] = wtb_4h
    C["L_wt_D"] = wtb_D
    C["L_wt_all3"] = wtb_1h & wtb_4h & wtb_D
    C["L_wt_2of3"] = (wtb_1h.astype(int) + wtb_4h.astype(int) + wtb_D.astype(int)) >= 2
    C["L_wt_ltf"] = wtb_5 & wtb_15
    C["L_wtx_5m"] = wtc_5
    C["L_wtx_15m"] = wtc_15
    C["L_wtx_1h"] = wtc_1h
    C["L_stx_5m"] = stc_5
    C["L_stx_15m"] = stc_15
    C["L_stx_1h"] = stc_1h
    C["L_dcx_5m"] = dcb_5
    C["L_dcx_15m"] = dcb_15
    C["L_dcx_1h"] = dcb_1h
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
    C["S_not_wt_5m"] = ~wtb_5
    C["S_not_wt_15m"] = ~wtb_15
    C["S_not_wt_1h"] = ~wtb_1h
    C["S_not_wt_4h"] = ~wtb_4h
    C["S_not_wt_D"] = ~wtb_D
    C["S_not_wt_all3"] = (~wtb_1h) & (~wtb_4h) & (~wtb_D)
    C["S_not_wt_2of3"] = ((~wtb_1h).astype(int) + (~wtb_4h).astype(int) + (~wtb_D).astype(int)) >= 2
    C["S_wtxr_5m"] = wtcr_5
    C["S_wtxr_15m"] = wtcr_15
    C["S_stxu_5m"] = stcu_5
    C["S_stxu_15m"] = stcu_15
    C["S_dcxu_15m"] = dcbu_15
    C["S_dcxu_1h"] = dcbu_1h
    C["S_sma200dn_D"] = (sma200D > 0) & (close < sma200D)
    C["S_sma200dn_1h"] = (sma200_1h > 0) & (close < sma200_1h)
    C["S_below_sma5pct"] = (sma200D > 0) & ((close - sma200D) / np.maximum(sma200D, 1e-9) < -0.05)
    return C, close


def fwd_returns(close, horizons):
    out = {}
    for h in horizons:
        if h >= close.shape[0]:
            continue
        rolled = np.roll(close, -h, axis=0)
        ret = np.where(close > 0, rolled / np.maximum(close, 1e-9) - 1, 0.0)
        ret[-h:] = 0.0
        out[h] = ret.astype(np.float32)
    return out


def score_per_symbol(mask, ret, side, sym_select=None):
    """Per-symbol Sharpe aggregation. Mask shape (bars, syms), ret same.
    For SHORT: negate returns (profit when price drops).
    sym_select: optional list of column indices to restrict evaluation to
                (e.g., top-ranked for LONG, bottom-ranked for SHORT).
    Averages across ALL selected syms that have >= MIN_TRADES_PER_SYM — INCLUDING negative Sharpes.
    No exclusion of bad performers — honest avg per user directive 2026-04-20.
    """
    if side == "S":
        ret = -ret
    n_syms = mask.shape[1]
    sym_range = sym_select if sym_select is not None else range(n_syms)
    per_sym = []
    per_sym_dd = []
    per_sym_wr = []
    per_sym_mean = []
    n_trades_total = 0
    pooled_rets = []
    excluded_noise = 0  # syms with too-few trades (stat-noise, not performance exclusion)
    for s in sym_range:
        m = mask[:, s]
        r = ret[:, s][m]
        n = len(r)
        n_trades_total += n
        if n < MIN_TRADES_PER_SYM:
            excluded_noise += 1
            continue
        std = float(r.std())
        if std <= 0:
            excluded_noise += 1
            continue
        mean = float(r.mean())
        sh = mean / std  # NEGATIVES KEPT — honest avg
        per_sym.append(sh)
        per_sym_mean.append(mean)
        wr = float((r > 0).mean() * 100)
        per_sym_wr.append(wr)
        r_clip = np.clip(r, -0.99, None)
        log_eq = np.cumsum(np.log1p(r_clip))
        peak = np.maximum.accumulate(log_eq)
        dd = float(1.0 - np.exp(log_eq - peak).min()) if log_eq.size else 0.0
        per_sym_dd.append(dd * 100)
        pooled_rets.append(r)
    total_eligible = len(list(sym_range)) if hasattr(sym_range, "__len__") else n_syms
    min_included = max(int(total_eligible * MIN_SYMS_PCT), 3)
    if len(per_sym) < min_included:
        return None
    per_sym_arr = np.asarray(per_sym)
    pool = np.concatenate(pooled_rets) if pooled_rets else np.asarray([])
    if pool.size == 0 or pool.std() <= 0:
        return None
    pool_sh = float(pool.mean() / pool.std())
    if pool_sh < MIN_POOL_SHARPE:
        return None
    sh_avg = float(per_sym_arr.mean())
    # Robust score: the min of avg and pool. Kills outlier-inflated averages (single-sym lucky run).
    sh_robust = min(sh_avg, pool_sh)
    return {
        "sharpe_avg": sh_avg,
        "sharpe_pool": pool_sh,
        "sharpe_robust": sh_robust,
        "sharpe_min": float(per_sym_arr.min()),
        "sharpe_p25": float(np.percentile(per_sym_arr, 25)),
        "sharpe_med": float(np.median(per_sym_arr)),
        "sharpe_p75": float(np.percentile(per_sym_arr, 75)),
        "sharpe_max": float(per_sym_arr.max()),
        "syms_included": len(per_sym),
        "n_trades_total": n_trades_total,
        "wr_avg": float(np.mean(per_sym_wr)),
        "mean_ret_avg": float(np.mean(per_sym_mean)),
        "dd_pct_avg": float(np.mean(per_sym_dd)),
        "dd_pct_max": float(np.max(per_sym_dd)),
    }


# Shared memory for workers (set in init_worker)
_C = None
_FWD = None
_KEYS_L = None
_KEYS_S = None
_LONG_SYMS = None   # indices of long-candidate syms (top-ranked by uptrend)
_SHORT_SYMS = None  # indices of short-candidate syms (bottom-ranked)


def init_worker(shared_state_path):
    global _C, _FWD, _KEYS_L, _KEYS_S, _LONG_SYMS, _SHORT_SYMS
    state = np.load(shared_state_path, allow_pickle=True).item()
    _C = state["C"]
    _FWD = state["FWD"]
    _KEYS_L = state["KEYS_L"]
    _KEYS_S = state["KEYS_S"]
    _LONG_SYMS = state.get("LONG_SYMS")
    _SHORT_SYMS = state.get("SHORT_SYMS")


def run_chunk(args):
    side, combos = args
    results = []
    keys = _KEYS_L if side == "L" else _KEYS_S
    sym_sel = _LONG_SYMS if side == "L" else _SHORT_SYMS
    for combo in combos:
        names = tuple(keys[i] for i in combo)
        mask = _C[names[0]].copy()
        for n in names[1:]:
            mask &= _C[n]
        if not mask.any():
            continue
        for h, ret in _FWD.items():
            m = score_per_symbol(mask, ret, side, sym_select=sym_sel)
            if m is None:
                continue
            results.append({
                "side": side,
                "combo": "+".join(n[2:] for n in names),
                "horizon": h,
                **m,
            })
    return results


def rank_syms_by_uptrend(loaded, syms):
    """Return (long_idx, short_idx) — lists of column indices.
    Uptrend score = total log return over window. Highest = long candidate, lowest = short candidate.
    """
    scores = []
    for i, s in enumerate(syms):
        close = np.asarray(loaded[s].get("close"), dtype=np.float64)
        if close is None or len(close) < 2 or close[0] <= 0 or close[-1] <= 0:
            scores.append((i, 0.0))
            continue
        scores.append((i, float(np.log(close[-1] / close[0]))))
    scores.sort(key=lambda x: x[1], reverse=True)
    keep = min(SIDE_RANK_KEEP, len(syms))
    long_idx = sorted(idx for idx, _ in scores[:keep])
    short_idx = sorted(idx for idx, _ in scores[-keep:])
    return long_idx, short_idx, scores


def chunker(iterable, size):
    buf = []
    for x in iterable:
        buf.append(x)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=22000, help="~1yr of 5m stock bars")
    ap.add_argument("--pick", type=int, default=3, help="combos of N conditions")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=500)
    ap.add_argument("--top-k", type=int, default=1000)
    ap.add_argument("--floor-sharpe", type=float, default=0.5, help="save only configs with sharpe_avg >= floor")
    ap.add_argument("--elite-sharpe", type=float, default=3.0, help="flag as elite if >= this")
    ap.add_argument("--out-db", type=str, default="")
    ap.add_argument("--time-budget-s", type=int, default=3600, help="hard stop after this many seconds")
    ap.add_argument("--sector", type=str, default="", help="Sector name from stocks_sectors.json — overrides MIX_12")
    ap.add_argument("--syms", type=str, default="", help="Comma-separated symbol list — overrides MIX_12 and --sector")
    ap.add_argument("--sectors-json", type=str, default="/home/niels/binance-sandbox/stocks_sectors.json")
    args = ap.parse_args()

    syms = [s for s in MIX_12]
    if args.syms:
        syms = [s.strip() for s in args.syms.split(",") if s.strip()]
    elif args.sector:
        try:
            with open(args.sectors_json) as f:
                secmap = json.load(f)
            if args.sector not in secmap:
                raise SystemExit(f"Sector '{args.sector}' not in {args.sectors_json}. Options: {list(secmap.keys())}")
            syms = [s for s in secmap[args.sector] if isinstance(s, str)]
        except FileNotFoundError:
            # Fallback to macbook path
            with open("/Users/niels/Documents/binance/stocks_sectors.json") as f:
                secmap = json.load(f)
            syms = [s for s in secmap[args.sector] if isinstance(s, str)]
    print(f"Using {len(syms)} symbols: {syms}")
    t0 = time.time()
    loaded, n_bars, npz_dir = load_slice(syms, args.bars)
    syms_used = list(loaded.keys())
    print(f"[{time.time()-t0:.1f}s] Loaded {len(syms_used)} syms × {n_bars} bars from {npz_dir}")

    C, close = build_conditions(loaded, syms_used)
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} conditions")

    fwd = fwd_returns(close, HORIZONS)
    print(f"[{time.time()-t0:.1f}s] Computed fwd rets for horizons {list(fwd.keys())}")

    keys_L = sorted([k for k in C if k.startswith("L_")])
    keys_S = sorted([k for k in C if k.startswith("S_")])

    # Pre-rank syms for long-candidate / short-candidate sides (user rule 2026-04-20)
    if SIDE_RANK_ENABLED and len(syms_used) > SIDE_RANK_KEEP:
        long_syms, short_syms, rank_scores = rank_syms_by_uptrend(loaded, syms_used)
        print(f"[{time.time()-t0:.1f}s] Uptrend ranking ({SIDE_RANK_KEEP}/{len(syms_used)}):")
        print(f"  LONG candidates:  {[syms_used[i] for i in long_syms]}")
        print(f"  SHORT candidates: {[syms_used[i] for i in short_syms]}")
    else:
        long_syms = short_syms = None
        print(f"[{time.time()-t0:.1f}s] No side-ranking (disabled or too few syms)")

    # Persist shared state to disk for fork-free worker init
    state_path = Path(f"/tmp/vec_stock_mega_state_{os.getpid()}.npy")
    np.save(str(state_path), {
        "C": C,
        "FWD": fwd,
        "KEYS_L": keys_L,
        "KEYS_S": keys_S,
        "LONG_SYMS": long_syms,
        "SHORT_SYMS": short_syms,
    })
    print(f"[{time.time()-t0:.1f}s] Saved shared state to {state_path}")

    n_L = sum(1 for _ in itertools.combinations(range(len(keys_L)), args.pick))
    n_S = sum(1 for _ in itertools.combinations(range(len(keys_S)), args.pick))
    total_combos = n_L + n_S
    total_tests = total_combos * len(fwd)
    print(f"[{time.time()-t0:.1f}s] Combos: L={n_L:,} S={n_S:,} total={total_combos:,} × {len(fwd)}h = {total_tests:,} tests")

    # SQLite setup
    out_db = Path(args.out_db) if args.out_db else BASE / "data" / "sweep_results" / f"vec_stock_mega_{int(time.time())}.sqlite"
    out_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(out_db))
    conn.execute("""CREATE TABLE IF NOT EXISTS results (
        side TEXT, combo TEXT, horizon INTEGER,
        sharpe_avg REAL, sharpe_pool REAL, sharpe_robust REAL,
        sharpe_min REAL, sharpe_p25 REAL, sharpe_med REAL, sharpe_p75 REAL, sharpe_max REAL,
        syms_included INTEGER, n_trades_total INTEGER,
        wr_avg REAL, mean_ret_avg REAL, dd_pct_avg REAL, dd_pct_max REAL,
        is_elite INTEGER DEFAULT 0)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_robust ON results(sharpe_robust DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sh ON results(sharpe_avg DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elite ON results(is_elite DESC, sharpe_robust DESC)")
    conn.commit()

    # Build work items (combo-index lists per side)
    def make_tasks(side, keys):
        idxs = list(range(len(keys)))
        for chunk in chunker(itertools.combinations(idxs, args.pick), args.chunk):
            yield (side, chunk)

    done_tests = 0
    kept = 0
    elite = 0
    report_t = time.time()

    with Pool(args.workers, initializer=init_worker, initargs=(str(state_path),)) as pool:
        tasks = itertools.chain(make_tasks("L", keys_L), make_tasks("S", keys_S))
        for batch in pool.imap_unordered(run_chunk, tasks, chunksize=1):
            for r in batch:
                if r["sharpe_robust"] >= args.floor_sharpe:
                    is_elite = 1 if r["sharpe_robust"] >= args.elite_sharpe else 0
                    conn.execute("""INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        r["side"], r["combo"], r["horizon"],
                        r["sharpe_avg"], r["sharpe_pool"], r["sharpe_robust"],
                        r["sharpe_min"], r["sharpe_p25"], r["sharpe_med"], r["sharpe_p75"], r["sharpe_max"],
                        r["syms_included"], r["n_trades_total"],
                        r["wr_avg"], r["mean_ret_avg"], r["dd_pct_avg"], r["dd_pct_max"],
                        is_elite,
                    ))
                    kept += 1
                    if is_elite:
                        elite += 1
            done_tests += len(batch) * len(fwd) // max(1, len(batch)) if batch else args.chunk * len(fwd)
            if time.time() - report_t > 20:
                conn.commit()
                elapsed = time.time() - t0
                rate = done_tests / max(1, elapsed)
                top = conn.execute("SELECT sharpe_robust, sharpe_avg, sharpe_pool, combo, horizon, syms_included, n_trades_total FROM results ORDER BY sharpe_robust DESC LIMIT 5").fetchall()
                print(f"[{elapsed:6.0f}s] kept={kept} elite={elite} rate={rate:,.0f}t/s  top5(by robust):")
                for row in top:
                    print(f"          robust={row[0]:.3f} avg={row[1]:.3f} pool={row[2]:.3f} syms={row[5]} n={row[6]} h={row[4]:<3} {row[3][:80]}")
                report_t = time.time()
                if elapsed > args.time_budget_s:
                    print(f"[{elapsed:.0f}s] HARD STOP — time budget reached")
                    pool.terminate()
                    break

    conn.commit()

    # Final report
    elapsed = time.time() - t0
    total_rows = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    elite_rows = conn.execute("SELECT COUNT(*) FROM results WHERE is_elite=1").fetchone()[0]
    print(f"\n=== DONE [{elapsed:.0f}s] total kept={total_rows}, elite={elite_rows}, db={out_db} ===\n")

    top_report = conn.execute("""SELECT side, combo, horizon, sharpe_robust, sharpe_avg, sharpe_pool, sharpe_min, sharpe_max, wr_avg, mean_ret_avg, dd_pct_avg, n_trades_total, syms_included
        FROM results ORDER BY sharpe_robust DESC LIMIT 30""").fetchall()
    print("TOP 30 by sharpe_robust (min of avg/pool — honest, outlier-resistant):")
    print(f"{'Side':<4} {'Rob':>6} {'Avg':>6} {'Pool':>6} {'Min':>6} {'Max':>6} {'WR%':>5} {'Mean%':>7} {'DD%':>6} {'N':>7} {'syms':>4} {'h':>4}  Combo")
    for row in top_report:
        side, combo, h, rob, sh, pool, mi, mx, wr, mr, dd, n, ns = row
        print(f"{side:<4} {rob:>6.3f} {sh:>6.3f} {pool:>6.3f} {mi:>6.3f} {mx:>6.3f} {wr:>5.1f} {mr*100:>7.3f} {dd:>6.2f} {n:>7d} {ns:>4d} {h:>4d}  {combo[:100]}")
    conn.close()
    try:
        state_path.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()
