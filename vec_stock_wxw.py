#!/usr/bin/env python3
"""Winners-with-Winners + full-scale validation.

Input: pick3 DB from vec_stock_mega. Top-N winners.
Mode 1 (--mode validate): run top-N verbatim on FULL stock dataset (128 stocks × 2yr ≈ 40k bars).
Mode 2 (--mode combine): take top-K, pair them (A_combo AND B_combo) = C(K,2) new "super-combos",
                        run on FULL stock dataset. Saves to combined_results table.
Mode 3 (--mode both): validate first, then combine.

Output: writes to same src DB, tables `validated_full` and `combined_results`.
"""
import argparse
import itertools
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
for p in (str(BASE), "/home/niels/binance-sandbox", "/Users/niels/Documents/binance"):
    if p not in sys.path:
        sys.path.insert(0, p)

from vec_stock_mega import build_conditions, fwd_returns, score_per_symbol, detect_npz_dir
import vec_stock_mega as vsm


import re
_CRYPTO_RE = re.compile(r"(USDT|USDC|BUSD)$|^BTC$|^ETH$|^BNB$|^SOL$|^XRP$|^ADA$|^DOGE$|^LINK$|^DOT$|^MATIC$|^AVAX$|^LTC$|^TRX$")


def load_stocks(n_bars, min_bars_per_sym, max_syms=128):
    npz_dir = detect_npz_dir()
    # Collect stock symbols (filter crypto) with bar count
    candidates = []
    for p in sorted(npz_dir.glob("*.npz")):
        sym = p.stem
        if _CRYPTO_RE.search(sym):
            continue
        try:
            z = np.load(str(p), allow_pickle=True)
        except Exception:
            continue
        L = len(z.get("close", []))
        if L < min_bars_per_sym:
            continue
        candidates.append((sym, L, str(p)))
    # Sort by bar count desc, take top max_syms
    candidates.sort(key=lambda x: -x[1])
    candidates = candidates[:max_syms]
    loaded = {}
    for sym, L, path in candidates:
        z = dict(np.load(path, allow_pickle=True))
        loaded[sym] = z
    if not loaded:
        raise RuntimeError(f"No stocks met min_bars_per_sym={min_bars_per_sym}")
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


def score_verbose(mask, ret, side, min_trades_per_sym, min_syms, sym_select=None):
    """Honest avg: include negative sharpe syms. Only skip for stat-noise (<min_trades) or zero-variance."""
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
    for s in sym_range:
        m = mask[:, s]
        r = ret[:, s][m]
        n = len(r)
        n_trades_total += n
        if n < min_trades_per_sym:
            continue
        std = float(r.std())
        if std <= 0:
            continue
        mean = float(r.mean())
        sh = mean / std  # NEGATIVES KEPT
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
    if len(per_sym) < min_syms:
        return None
    per_sym_arr = np.asarray(per_sym)
    pool = np.concatenate(pooled_rets) if pooled_rets else np.asarray([])
    if pool.size == 0 or pool.std() <= 0:
        return None
    return {
        "sharpe_avg": float(per_sym_arr.mean()),
        "sharpe_pool": float(pool.mean() / pool.std()),
        "sharpe_min": float(per_sym_arr.min()),
        "sharpe_max": float(per_sym_arr.max()),
        "syms_included": len(per_sym),
        "n_trades_total": n_trades_total,
        "wr_avg": float(np.mean(per_sym_wr)),
        "mean_ret_avg": float(np.mean(per_sym_mean)),
        "dd_pct_avg": float(np.mean(per_sym_dd)),
        "pos_syms": int((per_sym_arr > 0).sum()),
    }


def build_mask(C, side, combo_str):
    names = [f"{side}_{p}" for p in combo_str.split("+")]
    if not all(n in C for n in names):
        return None
    m = C[names[0]].copy()
    for n in names[1:]:
        m &= C[n]
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-db", required=True)
    ap.add_argument("--mode", choices=["validate", "combine", "both"], default="both")
    ap.add_argument("--top-n", type=int, default=100, help="validate top-N from results")
    ap.add_argument("--combine-k", type=int, default=20, help="pair-combine top-K")
    ap.add_argument("--bars", type=int, default=40000, help="~2yr of 5m stock bars")
    ap.add_argument("--min-bars-per-sym", type=int, default=35000)
    ap.add_argument("--min-trades-per-sym", type=int, default=50)
    ap.add_argument("--min-syms", type=int, default=40, help="of the full-loaded stocks, how many must qualify")
    ap.add_argument("--max-syms", type=int, default=128, help="cap total stocks loaded at this")
    args = ap.parse_args()

    t0 = time.time()
    print(f"[{time.time()-t0:.1f}s] Loading full stock dataset...")
    loaded, n_bars, npz_dir = load_stocks(args.bars, args.min_bars_per_sym, args.max_syms)
    syms = list(loaded.keys())
    print(f"[{time.time()-t0:.1f}s] Loaded {len(syms)} syms × {n_bars} bars from {npz_dir}")

    C, close = build_conditions(loaded, syms)
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} conditions on full")
    fwd = fwd_returns(close, [8, 16, 32, 64, 128, 256])

    # Uptrend ranking for long/short candidate selection (user rule 2026-04-20).
    # Select top half for LONG, bottom half for SHORT. Includes negatives — no post-hoc drops.
    scores = []
    for i, s in enumerate(syms):
        cl = np.asarray(loaded[s].get("close"), dtype=np.float64)
        scores.append((i, float(np.log(cl[-1] / cl[0])) if cl[0] > 0 and cl[-1] > 0 else 0.0))
    scores.sort(key=lambda x: x[1], reverse=True)
    keep = max(int(len(syms) * 0.6), args.min_syms)  # 60% for each side to ensure coverage
    long_syms = sorted(idx for idx, _ in scores[:keep])
    short_syms = sorted(idx for idx, _ in scores[-keep:])
    print(f"[{time.time()-t0:.1f}s] Long candidates: {keep}/{len(syms)}, Short candidates: {keep}/{len(syms)}")

    con = sqlite3.connect(args.src_db, timeout=60.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS validated_full (
        src_rowid INTEGER PRIMARY KEY, side TEXT, combo TEXT, horizon INT,
        sample_sharpe REAL, full_sharpe_avg REAL, full_sharpe_pool REAL,
        full_sharpe_min REAL, full_sharpe_max REAL, full_syms INT, full_pos_syms INT,
        full_trades INT, full_wr REAL, full_mean REAL, full_dd_avg REAL,
        dropoff_pct REAL, created_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS combined_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        side TEXT, combo_a TEXT, combo_b TEXT, combo_merged TEXT, horizon INT,
        full_sharpe_avg REAL, full_sharpe_pool REAL, full_sharpe_min REAL, full_sharpe_max REAL,
        full_syms INT, full_pos_syms INT, full_trades INT, full_wr REAL,
        full_mean REAL, full_dd_avg REAL, created_at REAL)""")
    con.commit()

    if args.mode in ("validate", "both"):
        rows = con.execute(
            "SELECT rowid, side, combo, horizon, sharpe_avg FROM results ORDER BY sharpe_avg DESC LIMIT ?",
            (args.top_n,)
        ).fetchall()
        print(f"\n[{time.time()-t0:.1f}s] MODE=validate — running top {len(rows)} on {len(syms)} syms × {n_bars} bars")
        print(f"{'Rk':>3} {'Sd':<2} {'Samp':>6} {'Full':>6} {'Pool':>6} {'Min':>6} {'Max':>6} {'Syms':>4} {'Pos':>3} {'N':>6} {'WR%':>5} {'Drop%':>6} {'h':>4}  Combo")
        validated = 0
        for rank, (rowid, side, combo, horizon, sample_sh) in enumerate(rows, 1):
            m = build_mask(C, side, combo)
            if m is None or not m.any():
                continue
            ret = fwd[horizon]
            r = score_verbose(m, ret, side, args.min_trades_per_sym, args.min_syms, sym_select=(long_syms if side == "L" else short_syms))
            if r is None:
                continue
            drop = (sample_sh - r["sharpe_avg"]) / max(abs(sample_sh), 1e-6) * 100
            print(f"{rank:>3} {side:<2} {sample_sh:>6.2f} {r['sharpe_avg']:>6.2f} {r['sharpe_pool']:>6.2f} {r['sharpe_min']:>6.2f} {r['sharpe_max']:>6.2f} {r['syms_included']:>4} {r['pos_syms']:>3} {r['n_trades_total']:>6} {r['wr_avg']:>5.1f} {drop:>5.0f}% {horizon:>4}  {combo[:80]}")
            con.execute("INSERT OR REPLACE INTO validated_full VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rowid, side, combo, horizon, sample_sh, r["sharpe_avg"], r["sharpe_pool"],
                 r["sharpe_min"], r["sharpe_max"], r["syms_included"], r["pos_syms"],
                 r["n_trades_total"], r["wr_avg"], r["mean_ret_avg"], r["dd_pct_avg"],
                 drop, time.time()))
            validated += 1
            if validated % 20 == 0:
                con.commit()
        con.commit()
        print(f"\n[{time.time()-t0:.1f}s] Validated {validated} / {len(rows)} on full dataset")

    if args.mode in ("combine", "both"):
        # Pull top-K by full_sharpe_avg if we have validations, else by sample sharpe_avg
        topk_rows = con.execute(
            "SELECT side, combo, horizon, full_sharpe_avg FROM validated_full WHERE full_sharpe_avg > 0 ORDER BY full_sharpe_avg DESC LIMIT ?",
            (args.combine_k,)
        ).fetchall()
        if len(topk_rows) < 2:
            topk_rows = [(s, c, h, sh) for s, c, h, sh in con.execute(
                "SELECT side, combo, horizon, sharpe_avg FROM results ORDER BY sharpe_avg DESC LIMIT ?", (args.combine_k,)
            ).fetchall()]
        n_pairs = len(topk_rows) * (len(topk_rows) - 1) // 2
        print(f"\n[{time.time()-t0:.1f}s] MODE=combine — pair-combining top-{len(topk_rows)} = {n_pairs} super-combos")
        print(f"{'Rk':>3} {'Sd':<2} {'Sh':>6} {'Pool':>6} {'Min':>6} {'Max':>6} {'Syms':>4} {'Pos':>3} {'N':>6} {'WR%':>5} {'h':>4}  Merged")
        combined_results = []
        for (sa, ca, ha, _), (sb, cb, hb, _) in itertools.combinations(topk_rows, 2):
            # Only combine same-side combos (LONG+LONG or SHORT+SHORT)
            if sa != sb:
                continue
            side = sa
            # Merge: AND both combos, try at each common horizon subset
            merged_names = list({f"{side}_{p}" for p in ca.split("+")} | {f"{side}_{p}" for p in cb.split("+")})
            if not all(n in C for n in merged_names):
                continue
            m = C[merged_names[0]].copy()
            for n in merged_names[1:]:
                m &= C[n]
            if not m.any():
                continue
            # Prefer horizon of higher-Sharpe side
            horizon = ha
            ret = fwd[horizon]
            r = score_verbose(m, ret, side, max(args.min_trades_per_sym // 2, 5), max(args.min_syms // 2, 10), sym_select=(long_syms if side == "L" else short_syms))
            if r is None:
                continue
            merged_combo = "+".join(sorted(set([*ca.split("+"), *cb.split("+")])))
            combined_results.append({"side": side, "ca": ca, "cb": cb, "merged": merged_combo, "h": horizon, **r})
            con.execute("INSERT INTO combined_results VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (side, ca, cb, merged_combo, horizon, r["sharpe_avg"], r["sharpe_pool"],
                 r["sharpe_min"], r["sharpe_max"], r["syms_included"], r["pos_syms"],
                 r["n_trades_total"], r["wr_avg"], r["mean_ret_avg"], r["dd_pct_avg"], time.time()))
        con.commit()
        combined_results.sort(key=lambda x: x["sharpe_avg"], reverse=True)
        print(f"\n[{time.time()-t0:.1f}s] {len(combined_results)} merged combos produced valid results")
        print(f"\nTOP 20 merged combos (same-side intersections):")
        print(f"{'Rk':>3} {'Sd':<2} {'Sh':>6} {'Pool':>6} {'Min':>6} {'Max':>6} {'Syms':>4} {'Pos':>3} {'N':>6} {'WR%':>5} {'h':>4}  Merged")
        for rk, r in enumerate(combined_results[:20], 1):
            print(f"{rk:>3} {r['side']:<2} {r['sharpe_avg']:>6.2f} {r['sharpe_pool']:>6.2f} {r['sharpe_min']:>6.2f} {r['sharpe_max']:>6.2f} {r['syms_included']:>4} {r['pos_syms']:>3} {r['n_trades_total']:>6} {r['wr_avg']:>5.1f} {r['h']:>4}  {r['merged'][:90]}")

    con.close()
    print(f"\n=== DONE [{time.time()-t0:.0f}s] ===")


if __name__ == "__main__":
    main()
