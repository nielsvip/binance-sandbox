#!/usr/bin/env python3
"""Winners-with-Winners + full-scale validation — REALISTIC EXIT SCORING.

Rewrite 2026-04-20: scoring now uses technical exits (wt_cross_bear_15m for LONG,
wt_cross_bull_15m for SHORT) via precompute_exit_indices + score_realistic, matching
vec_stock_mega.py. Forward-horizon scoring is OBSOLETE.

Input: sector DB from vec_stock_mega.
Mode validate: top-N combos → rerun on 128-sym × 2yr with realistic exits.
Mode combine: top-K winners pair-merged → rerun on full dataset.
Mode both: validate then combine.
"""
import argparse
import itertools
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
for p in (str(BASE), "/home/niels/binance-sandbox", "/Users/niels/Documents/binance"):
    if p not in sys.path:
        sys.path.insert(0, p)

from vec_stock_mega import (
    build_conditions, precompute_exit_indices, score_realistic,
    detect_npz_dir, stack_bool, LONG_EXIT_FIELD, SHORT_EXIT_FIELD,
)
import vec_stock_mega as vsm

_CRYPTO_RE = re.compile(r"(USDT|USDC|BUSD)$|^BTC$|^ETH$|^BNB$|^SOL$|^XRP$|^ADA$|^DOGE$|^LINK$|^DOT$|^MATIC$|^AVAX$|^LTC$|^TRX$")


def load_stocks(n_bars, min_bars_per_sym, max_syms=128):
    npz_dir = detect_npz_dir()
    candidates = []
    for p in sorted(npz_dir.glob("*.npz")):
        sym = p.stem
        if _CRYPTO_RE.search(sym): continue
        try:
            z = np.load(str(p), allow_pickle=True)
        except Exception:
            continue
        L = len(z.get("close", []))
        if L < min_bars_per_sym: continue
        candidates.append((sym, L, str(p)))
    candidates.sort(key=lambda x: -x[1])
    candidates = candidates[:max_syms]
    loaded = {}
    for sym, L, path in candidates:
        loaded[sym] = dict(np.load(path, allow_pickle=True))
    if not loaded:
        raise RuntimeError(f"No stocks met min_bars={min_bars_per_sym}")
    min_len = min(len(z["close"]) for z in loaded.values())
    use = min(n_bars, min_len)
    for s in list(loaded.keys()):
        z = loaded[s]
        for k in list(z.keys()):
            a = np.asarray(z[k])
            if a.ndim == 1 and len(a) >= use:
                z[k] = a[-use:]
        loaded[s] = z
    return loaded, use, npz_dir


def build_mask(C, side, combo_str):
    names = [f"{side}_{p}" for p in combo_str.split("+")]
    if not all(n in C for n in names): return None
    m = C[names[0]].copy()
    for n in names[1:]:
        m &= C[n]
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-db", required=True)
    ap.add_argument("--mode", choices=["validate", "combine", "both"], default="both")
    ap.add_argument("--top-n", type=int, default=100)
    ap.add_argument("--combine-k", type=int, default=20)
    ap.add_argument("--bars", type=int, default=40000)
    ap.add_argument("--min-bars-per-sym", type=int, default=35000)
    ap.add_argument("--max-syms", type=int, default=128)
    ap.add_argument("--min-syms", type=int, default=40)
    ap.add_argument("--max-hold", type=int, default=vsm.MAX_HOLD_BARS)
    args = ap.parse_args()

    t0 = time.time()
    print(f"[{time.time()-t0:.1f}s] Loading full stock dataset...")
    loaded, n_bars, npz_dir = load_stocks(args.bars, args.min_bars_per_sym, args.max_syms)
    syms = list(loaded.keys())
    print(f"[{time.time()-t0:.1f}s] Loaded {len(syms)} syms × {n_bars} bars from {npz_dir}")

    C, close = build_conditions(loaded, syms)
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} conditions")
    long_exit_mask = stack_bool(loaded, LONG_EXIT_FIELD, syms)
    short_exit_mask = stack_bool(loaded, SHORT_EXIT_FIELD, syms)
    exit_idx_L = precompute_exit_indices(long_exit_mask, args.max_hold)
    exit_idx_S = precompute_exit_indices(short_exit_mask, args.max_hold)
    print(f"[{time.time()-t0:.1f}s] Precomputed exit indices")

    # Uptrend ranking: top 60% for LONG, bottom 60% for SHORT
    scores = []
    for i, s in enumerate(syms):
        cl = np.asarray(loaded[s].get("close"), dtype=np.float64)
        scores.append((i, float(np.log(cl[-1] / cl[0])) if cl[0] > 0 and cl[-1] > 0 else 0.0))
    scores.sort(key=lambda x: x[1], reverse=True)
    keep = max(int(len(syms) * 0.6), args.min_syms)
    long_syms = sorted(idx for idx, _ in scores[:keep])
    short_syms = sorted(idx for idx, _ in scores[-keep:])
    print(f"[{time.time()-t0:.1f}s] Long cands={keep}/{len(syms)}, short cands={keep}/{len(syms)}")

    # Ensure wxw table schemas drop stale
    con = sqlite3.connect(args.src_db, timeout=60.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("DROP TABLE IF EXISTS validated_full")
    con.execute("DROP TABLE IF EXISTS combined_results")
    con.execute("""CREATE TABLE validated_full (
        src_rowid INTEGER PRIMARY KEY, side TEXT, combo TEXT,
        sample_robust REAL, full_sharpe_avg REAL, full_sharpe_pool REAL, full_sharpe_robust REAL,
        full_sharpe_min REAL, full_sharpe_max REAL, full_syms INT, full_trades INT,
        full_wr REAL, full_mean REAL, dropoff_pct REAL, created_at REAL)""")
    con.execute("""CREATE TABLE combined_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        side TEXT, combo_a TEXT, combo_b TEXT, combo_merged TEXT,
        full_sharpe_avg REAL, full_sharpe_pool REAL, full_sharpe_robust REAL,
        full_sharpe_min REAL, full_sharpe_max REAL,
        full_syms INT, full_trades INT, full_wr REAL, full_mean REAL, created_at REAL)""")
    con.commit()

    # Temporarily lower MIN_SYMS_PCT for full-dataset scoring (larger universe → relax)
    orig_pct = vsm.MIN_SYMS_PCT
    vsm.MIN_SYMS_PCT = 0.3  # require at least 30% of selected candidates to trigger

    if args.mode in ("validate", "both"):
        rows = con.execute(
            "SELECT rowid, side, combo, sharpe_robust FROM results ORDER BY sharpe_robust DESC LIMIT ?",
            (args.top_n,)
        ).fetchall()
        print(f"\n[{time.time()-t0:.1f}s] MODE=validate — top {len(rows)} on {len(syms)} syms")
        print(f"{'Rk':>3} {'Sd':<2} {'Samp':>6} {'Full':>6} {'Pool':>6} {'Min':>6} {'Max':>6} {'Sy':>4} {'N':>6} {'WR%':>5} {'Drop%':>6}  Combo")
        validated = 0
        for rank, (rowid, side, combo, sample_rob) in enumerate(rows, 1):
            m = build_mask(C, side, combo)
            if m is None or not m.any(): continue
            exit_idx = exit_idx_L if side == "L" else exit_idx_S
            sym_sel = long_syms if side == "L" else short_syms
            r = score_realistic(m, close, exit_idx, side, sym_select=sym_sel)
            if r is None: continue
            drop = (sample_rob - r["sharpe_robust"]) / max(abs(sample_rob), 1e-6) * 100
            print(f"{rank:>3} {side:<2} {sample_rob:>6.3f} {r['sharpe_robust']:>6.3f} {r['sharpe_pool']:>6.3f} {r['sharpe_min']:>6.3f} {r['sharpe_max']:>6.3f} {r['syms_included']:>4} {r['n_trades_total']:>6} {r['wr_avg']:>5.1f} {drop:>5.0f}%  {combo[:60]}")
            con.execute("INSERT OR REPLACE INTO validated_full VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rowid, side, combo, sample_rob, r["sharpe_avg"], r["sharpe_pool"], r["sharpe_robust"],
                 r["sharpe_min"], r["sharpe_max"], r["syms_included"], r["n_trades_total"],
                 r["wr_avg"], r["mean_ret_avg"], drop, time.time()))
            validated += 1
            if validated % 20 == 0: con.commit()
        con.commit()
        print(f"\n[{time.time()-t0:.1f}s] Validated {validated}/{len(rows)} on full dataset")

    if args.mode in ("combine", "both"):
        # Rank by validated full-dataset robust first; fallback to sample robust
        topk_rows = con.execute(
            "SELECT side, combo, full_sharpe_robust FROM validated_full WHERE full_sharpe_robust IS NOT NULL ORDER BY full_sharpe_robust DESC LIMIT ?",
            (args.combine_k,)
        ).fetchall()
        if len(topk_rows) < 2:
            topk_rows = [(s, c, sr) for s, c, sr in con.execute(
                "SELECT side, combo, sharpe_robust FROM results ORDER BY sharpe_robust DESC LIMIT ?", (args.combine_k,)
            ).fetchall()]
        n_pairs = len(topk_rows) * (len(topk_rows) - 1) // 2
        print(f"\n[{time.time()-t0:.1f}s] MODE=combine — pair-combining top-{len(topk_rows)} = {n_pairs} super-combos")
        print(f"{'Rk':>3} {'Sd':<2} {'Rob':>6} {'Avg':>6} {'Pool':>6} {'Min':>6} {'Max':>6} {'Sy':>4} {'N':>6} {'WR%':>5}  Merged")
        merged_results = []
        for (sa, ca, _), (sb, cb, _) in itertools.combinations(topk_rows, 2):
            if sa != sb: continue
            side = sa
            merged_names = list({f"{side}_{p}" for p in ca.split("+")} | {f"{side}_{p}" for p in cb.split("+")})
            if not all(n in C for n in merged_names): continue
            m = C[merged_names[0]].copy()
            for n in merged_names[1:]:
                m &= C[n]
            if not m.any(): continue
            exit_idx = exit_idx_L if side == "L" else exit_idx_S
            sym_sel = long_syms if side == "L" else short_syms
            r = score_realistic(m, close, exit_idx, side, sym_select=sym_sel)
            if r is None: continue
            merged_combo = "+".join(sorted(set([*ca.split("+"), *cb.split("+")])))
            merged_results.append({"side": side, "ca": ca, "cb": cb, "merged": merged_combo, **r})
            con.execute("INSERT INTO combined_results VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (side, ca, cb, merged_combo, r["sharpe_avg"], r["sharpe_pool"], r["sharpe_robust"],
                 r["sharpe_min"], r["sharpe_max"], r["syms_included"], r["n_trades_total"],
                 r["wr_avg"], r["mean_ret_avg"], time.time()))
        con.commit()
        merged_results.sort(key=lambda x: x["sharpe_robust"], reverse=True)
        print(f"\n[{time.time()-t0:.1f}s] {len(merged_results)} merged combos valid")
        print(f"\nTOP 20 merged combos:")
        for rk, r in enumerate(merged_results[:20], 1):
            print(f"{rk:>3} {r['side']:<2} {r['sharpe_robust']:>6.3f} {r['sharpe_avg']:>6.3f} {r['sharpe_pool']:>6.3f} {r['sharpe_min']:>6.3f} {r['sharpe_max']:>6.3f} {r['syms_included']:>4} {r['n_trades_total']:>6} {r['wr_avg']:>5.1f}  {r['merged'][:75]}")

    vsm.MIN_SYMS_PCT = orig_pct
    con.close()
    print(f"\n=== DONE [{time.time()-t0:.0f}s] ===")


if __name__ == "__main__":
    main()
