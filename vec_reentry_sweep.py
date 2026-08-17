#!/usr/bin/env python3
"""Reentry-variant sweep — test ALL reentry triggers × delays × HTF filters.

For each exit-event type (WT crossunder, stoch crossunder, DC crossunder), we:
  1. Detect exit-event bars.
  2. Test reentry conditions N bars later (delay grid).
  3. Measure forward return FROM the reentry bar.

This tests the `evaluate_reentry` / `reentry_enforcement_loop` logic space:
  - "how long to wait after exit before reentering"
  - "what condition confirms reentry is safe"
  - "does HTF trend still matter after exit"
  - etc.

Small 15-sym sample (fast iteration). Results go to a dedicated SQLite so they
don't pollute the main vec_backlog DB.
"""
import argparse
import itertools
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import (
    CRYPTO_SYMS, TRADIER_SYMS,
    build_crypto_conditions, build_tradier_conditions,
    fwd_returns, load_slice, score, stack_bool, stack_field,
)

# Sample sets (15 each)
CRYPTO_15 = "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC,ADAUSDC,AVAXUSDC,DOTUSDT,LINKUSDC,LTCUSDC,UNIUSDC,ATOMUSDT,BCHUSDT,ETCUSDT,FILUSDT".split(",")
TRADIER_15 = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,META,TSLA,SPY,QQQ,XLF,XLE,GLD,USO,IWM".split(",")


def shift_forward(arr, n):
    """Shift 2-D bool array `n` bars FORWARD in time (row axis). New front rows = False."""
    out = np.zeros_like(arr)
    if n > 0 and n < arr.shape[0]:
        out[n:] = arr[:-n]
    elif n == 0:
        out = arr.copy()
    return out


def build_reentry_events(loaded, mode):
    """Build dict of exit-event bool arrays (bars x symbols) per side."""
    # Crypto uses 3m base, tradier 5m. Use 5m for both (crypto has it too).
    ev = {}
    if mode == "crypto":
        ev["L_exit_wt_xr5"] = stack_bool(loaded, "wt_cross_bear_5m")      # WT bearish cross on 5m = LONG exit
        ev["L_exit_wt_xr15"] = stack_bool(loaded, "wt_cross_bear_15m")
        ev["L_exit_stoch_xu5"] = stack_bool(loaded, "stoch_crossunder_5m")
        ev["L_exit_stoch_xu15"] = stack_bool(loaded, "stoch_crossunder_15m")
        ev["L_exit_dc_xu15"] = stack_bool(loaded, "dc_basis_crossunder_15m")
        ev["S_exit_wt_x5"] = stack_bool(loaded, "wt_cross_bull_5m")
        ev["S_exit_wt_x15"] = stack_bool(loaded, "wt_cross_bull_15m")
        ev["S_exit_stoch_x5"] = stack_bool(loaded, "stoch_crossover_5m")
        ev["S_exit_stoch_x15"] = stack_bool(loaded, "stoch_crossover_15m")
        ev["S_exit_dc_x15"] = stack_bool(loaded, "dc_basis_crossover_15m")
    else:
        ev["L_exit_wt_xr5"] = stack_bool(loaded, "wt_cross_bear_5m")
        ev["L_exit_wt_xr15"] = stack_bool(loaded, "wt_cross_bear_15m")
        ev["L_exit_stoch_xu5"] = stack_bool(loaded, "stoch_crossunder_5m")
        ev["L_exit_stoch_xu15"] = stack_bool(loaded, "stoch_crossunder_15m")
        ev["L_exit_dc_xu15"] = stack_bool(loaded, "dc_basis_crossunder_15m")
        ev["S_exit_wt_x5"] = stack_bool(loaded, "wt_cross_bull_5m")
        ev["S_exit_wt_x15"] = stack_bool(loaded, "wt_cross_bull_15m")
        ev["S_exit_stoch_x5"] = stack_bool(loaded, "stoch_crossover_5m")
        ev["S_exit_stoch_x15"] = stack_bool(loaded, "stoch_crossover_15m")
        ev["S_exit_dc_x15"] = stack_bool(loaded, "dc_basis_crossover_15m")
    return ev


def build_reentry_triggers(C, side):
    """Filter C for reentry-appropriate triggers on the given side."""
    triggers = {}
    prefix = side + "_"
    for k, v in C.items():
        if not k.startswith(prefix):
            continue
        # Focus on "trigger" conditions — crosses, specific thresholds
        base = k[2:]
        if any(t in base for t in ("wt_x", "stoch_x", "dc_x", "dc_xu", "stoch_xu", "wt_xr")):
            triggers[k] = v
        # Plus a handful of HTF filters that act as "1h still trending" gates
        if base in ("wt_1h", "wt_4h", "wt_D", "wt_all3", "wt_2of3", "not_wt_1h", "not_wt_4h", "not_wt_all3"):
            triggers[k] = v
    return triggers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--bars", type=int, default=40000)
    ap.add_argument("--delays", nargs="+", type=int, default=[1, 3, 5, 8, 12, 20, 30, 60, 120, 240, 480],
                    help="bars to wait after exit before checking reentry trigger")
    ap.add_argument("--horizons", nargs="+", type=int, default=[8, 16, 32, 64, 128, 256, 512])
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--db", default="")
    ap.add_argument("--npz-dir", default="")
    from vector_mandatory_coverage import add_coverage_claim_arguments, enforce_coverage_claim
    add_coverage_claim_arguments(ap)
    args = ap.parse_args()
    coverage_contract = enforce_coverage_claim(args, runner="vec_reentry_sweep.py")
    print(f"V8_VECTOR_GROUND_RULE: {coverage_contract['coverage_status']} shortlist_sha256={coverage_contract['shortlist_sha256']}", flush=True)

    symbols = CRYPTO_15 if args.mode == "crypto" else TRADIER_15
    t0 = time.time()
    loaded, n_bars, npz_dir = load_slice(args.mode, symbols, args.bars)
    print(f"[{time.time()-t0:.1f}s] Loaded {len(loaded)} symbols × {n_bars} bars from {npz_dir}", flush=True)
    if args.mode == "crypto":
        C, close = build_crypto_conditions(loaded)
    else:
        C, close = build_tradier_conditions(loaded)
    fwd = fwd_returns(close, args.horizons)
    events = build_reentry_events(loaded, args.mode)
    print(f"[{time.time()-t0:.1f}s] {len(C)} conds, {len(fwd)} horizons, {len(events)} exit-event types", flush=True)

    db_path = Path(args.db) if args.db else Path(__file__).parent / "data" / "sweep_results" / f"vec_reentry_{args.mode}.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS reentry (
        event TEXT, trigger TEXT, side TEXT, delay INT, horizon INT,
        n_trades INT, sharpe REAL, wr REAL, mean_ret REAL, std REAL, pf REAL,
        composite REAL, created_at REAL)""")
    con.execute("DELETE FROM reentry")
    con.commit()

    results = []
    n_done = 0
    t_start = time.time()
    for event_name, event_mask in events.items():
        side = event_name[0]  # 'L' or 'S'
        # Trigger conditions scoped to this side
        triggers = build_reentry_triggers(C, side)
        for delay in args.delays:
            # Shift the exit-event forward by `delay` bars — each True == "exit happened `delay` bars ago"
            exit_N_bars_ago = shift_forward(event_mask, delay)
            for trig_name, trig_mask in triggers.items():
                combined = exit_N_bars_ago & trig_mask
                if combined.sum() < args.min_trades:
                    continue
                for h in args.horizons:
                    if h not in fwd:
                        continue
                    m = score(combined, fwd[h], args.min_trades)
                    if m is None:
                        continue
                    comp = m["sharpe"] + (m["wr"] / 200.0) + m["mean"] * 10
                    results.append({
                        "event": event_name[2:], "trigger": trig_name[2:], "side": side,
                        "delay": delay, "horizon": h,
                        **m, "composite": comp,
                    })
                    n_done += 1
                    if n_done % 500 == 0:
                        print(f"[{time.time()-t0:.1f}s] {n_done} tests, best sharpe so far: {max(r['sharpe'] for r in results):.3f}", flush=True)

    print(f"\n[{time.time()-t0:.1f}s] DONE: {n_done} tests, {len(results)} passed min_trades", flush=True)
    # Write DB
    con.executemany(
        "INSERT INTO reentry (event, trigger, side, delay, horizon, n_trades, sharpe, wr, mean_ret, std, pf, composite, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["event"], r["trigger"], r["side"], r["delay"], r["horizon"], r["n"], r["sharpe"], r["wr"], r["mean"], r["std"], r["pf"], r["composite"], time.time()) for r in results],
    )
    con.commit()

    results.sort(key=lambda r: r["sharpe"], reverse=True)
    print(f"\n=== TOP 30 BY SHARPE ({args.mode}) ===", flush=True)
    print(f"{'Side':>4} {'Event':>18} {'Delay':>5} {'H':>4} {'N':>5} {'Sharpe':>7} {'WR%':>6} {'Mean%':>7} {'PF':>6}  Trigger", flush=True)
    for r in results[:30]:
        print(f"{r['side']:>4} {r['event']:>18} {r['delay']:>5d} {r['horizon']:>4d} {r['n']:>5d} {r['sharpe']:>7.3f} {r['wr']:>6.1f} {r['mean']*100:>7.3f} {r['pf']:>6.2f}  {r['trigger']}", flush=True)

    # Best delay per side
    print(f"\n=== BEST SHARPE PER DELAY (aggregated across all trigger/event/horizon combos) ===", flush=True)
    by_delay = {}
    for r in results:
        key = (r["side"], r["delay"])
        if key not in by_delay or r["sharpe"] > by_delay[key]["sharpe"]:
            by_delay[key] = r
    for side in ("L", "S"):
        print(f"  {side}:", flush=True)
        for d in sorted(args.delays):
            if (side, d) in by_delay:
                r = by_delay[(side, d)]
                print(f"    delay={d:>3}  best_sharpe={r['sharpe']:.3f} wr={r['wr']:.1f} mean={r['mean']*100:.3f}% n={r['n']} — {r['event']}+{r['trigger']} h={r['horizon']}", flush=True)


if __name__ == "__main__":
    main()
