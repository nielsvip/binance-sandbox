#!/usr/bin/env python
"""dc64_walkforward.py — WALK-FORWARD validation to kill overfit + keep 365D as regime compass.

The 30D-optimize-then-30D-evaluate greedy overfits (winners fail 365D). Real fix: optimize the
switch set on an IN-SAMPLE window, then PAPER-TRADE the winner on the untouched OUT-OF-SAMPLE
window that follows it. A config is only kept if it GENERALIZES (OOS delta > 0) and does not
blow up over the year (365D delta >= 0 = survives regime changes / overnight trend flips).

Scenarios (IS_days -> OOS_days), IS is the window BEFORE the OOS window:
   7/7, 30/30, 60/30, 90/30, 120/60   (+ 365D compass)
"paper trade" = the winner's realized gain on the OOS window it was NOT optimized on.

Per sym_side we report, for each scenario: IS gain(base->opt), OOS gain(base->opt paper),
OOS delta, and a robustness verdict. Aggregate tells us which IS/OOS choice best curbs overfit.

Usage:
  python tools/dc64_walkforward.py --syms AAVEUSDC_LONG,ACEUSDT_SHORT,AAPL_LONG,COE_SHORT
  python tools/dc64_walkforward.py --all --venue crypto --workers 3
"""
import os, sys, json, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
import v12_quick_engine as V
from tools import dc_simple_8_sweep as DC
from tools import dc64_greedy as G

SCENARIOS = [(7, 7), (30, 30), (60, 30), (90, 30), (120, 60)]
OUT = os.path.join(ROOT, "data", "reports", "dc64_walkforward.json")
DAY = 86400
PM = None


def _slice(npz, end_ts, days):
    ts = npz.get("timestamps", npz.get("timestamp_3m"))
    if ts is None or len(ts) == 0:
        return None
    ts = np.asarray(ts)
    start_ts = end_ts - days * DAY
    lo = int(np.searchsorted(ts, start_ts)); hi = int(np.searchsorted(ts, end_ts, side="right"))
    if hi - lo < 100:
        return None
    out = {}
    n = len(ts)
    for k, v in npz.items():
        if isinstance(v, np.ndarray) and v.ndim > 0 and len(v) == n:
            out[k] = v[lo:hi].copy()
        else:
            out[k] = v
    return out


def _cfg(is_crypto, ov):
    c = V.QuickConfig(); c.MODE = "crypto" if is_crypto else "tradier"
    for k, v in ov.items():
        try: setattr(c, k, v)
        except Exception: pass
    return c


def _gain(sliced, sym, is_long, ov, is_crypto):
    r = V.simulate_one(sliced, sym, is_long, _cfg(is_crypto, ov))
    if not r:
        return None, 0
    return r.get("gain_pct_2000norm", 0.0), r.get("trades", 0)


def _greedy_optimize(sliced, sym, is_long, is_crypto, base_ov):
    """Greedy group optimization on a single slice (mirror of dc64_greedy, in-sample)."""
    cum = dict(base_ov)
    cg, _ = _gain(sliced, sym, is_long, cum, is_crypto)
    cg = cg if cg is not None else 0.0
    base_gain = cg
    for gname, opts in G._group_options():
        best = None
        for label, dov in opts:
            test = dict(cum); test.update(dov)
            g, tr = _gain(sliced, sym, is_long, test, is_crypto)
            if g is None:
                continue
            if best is None or g > best[1]:
                best = (label, g, dov)
        if best and best[1] - cg > 1e-9:
            cum.update(best[2]); cg = best[1]
    return cum, base_gain, cg


def walkforward_one(sym_side):
    base_ov, sym, is_long, crypto = G._base_overrides(sym_side, PM)
    stores = V.load_npz("crypto" if crypto else "tradier", [sym], "2024-01-01")
    npz = stores.get(sym)
    if npz is None or len(npz.get("timestamps", [])) < 500:
        return {"sym_side": sym_side, "error": "npz missing/short"}
    ts = np.asarray(npz["timestamps"]); t_end = int(ts[-1])
    res = {"sym_side": sym_side, "venue": "crypto" if crypto else "stocks", "scenarios": {}}
    oos_deltas = []
    for IS, OOS in SCENARIOS:
        oos_end = t_end; oos_start = t_end - OOS * DAY; is_end = oos_start
        is_sl = _slice(npz, is_end, IS); oos_sl = _slice(npz, oos_end, OOS)
        if is_sl is None or oos_sl is None:
            res["scenarios"][f"{IS}/{OOS}"] = {"skip": "insufficient bars"}; continue
        cum, is_base, is_opt = _greedy_optimize(is_sl, sym, is_long, crypto, base_ov)   # optimize IS
        oos_base, oos_bt = _gain(oos_sl, sym, is_long, base_ov, crypto)                 # OOS baseline
        oos_new, oos_nt = _gain(oos_sl, sym, is_long, cum, crypto)                      # OOS paper-trade
        oos_base = oos_base or 0.0; oos_new = oos_new or 0.0
        oos_d = oos_new - oos_base
        oos_deltas.append(oos_d)
        res["scenarios"][f"{IS}/{OOS}"] = {
            "is_base": round(is_base, 2), "is_opt": round(is_opt, 2), "is_gain_lift": round(is_opt - is_base, 2),
            "oos_base": round(oos_base, 2), "oos_paper": round(oos_new, 2), "oos_delta": round(oos_d, 2),
            "oos_trades": oos_nt, "generalizes": bool(oos_d > 1e-9),
            "switches": {k: (v if isinstance(v, (int, float, str, bool)) else str(v)) for k, v in cum.items() if base_ov.get(k) != v}}
    # 365D compass on the 30/30 winner's switch set (or the most common winner)
    win30 = res["scenarios"].get("30/30", {}).get("switches", {})
    sl365 = _slice(npz, t_end, 365)
    if sl365 is not None and win30:
        g_b, _ = _gain(sl365, sym, is_long, base_ov, crypto)
        g_n, _ = _gain(sl365, sym, is_long, {**base_ov, **win30}, crypto)
        res["compass_365d"] = {"base": round(g_b or 0, 2), "new": round(g_n or 0, 2), "delta": round((g_n or 0) - (g_b or 0), 2)}
    n_gen = sum(1 for d in oos_deltas if d > 1e-9)
    res["oos_generalizes_count"] = n_gen
    res["oos_mean_delta"] = round(float(np.mean(oos_deltas)), 2) if oos_deltas else 0.0
    res["robust"] = bool(n_gen >= max(3, len(SCENARIOS) - 1) and res.get("compass_365d", {}).get("delta", -1) >= 0)
    return res


def main():
    global PM
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default=""); ap.add_argument("--all", action="store_true")
    ap.add_argument("--venue", choices=["crypto", "stocks", "both"], default="both")
    ap.add_argument("--workers", type=int, default=3); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    PM, _, _ = DC.load_per_sym_maps()
    if a.syms:
        targets = [s.strip() for s in a.syms.split(",") if s.strip()]
    else:
        keys = [k for k in PM if not k.startswith("_")]
        def isc(k):
            b = k[:-5] if k.endswith("_LONG") else k[:-6]
            return b.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD"))
        targets = sorted([k for k in keys if a.venue == "both" or isc(k) == (a.venue == "crypto")])
        if a.limit:
            targets = targets[:a.limit]
    print(f"[walkforward] {len(targets)} sym_sides, scenarios {SCENARIOS} + 365D compass, workers={a.workers}", flush=True)
    # incremental JSONL (crash-safe: partial results survive OOM). Resume: skip already-done.
    jsonl = OUT.replace(".json", f"_{a.venue}.jsonl")
    done_ss = set()
    if os.path.exists(jsonl):
        for _l in open(jsonl):
            try: done_ss.add(json.loads(_l).get("sym_side"))
            except Exception: pass
    targets = [t for t in targets if t not in done_ss]
    if done_ss:
        print(f"[walkforward] resuming — {len(done_ss)} already done, {len(targets)} remaining", flush=True)
    _jf = open(jsonl, "a")
    results = [json.loads(_l) for _l in open(jsonl)] if os.path.exists(jsonl) else []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(walkforward_one, s): s for s in targets}
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception as e:
                r = {"sym_side": futs[f], "error": f"exc {e}"}
            results.append(r)
            _jf.write(json.dumps(r) + "\n"); _jf.flush()
            if "error" in r:
                print(f"[{len(results)}/{len(targets)}] {r['sym_side']}: ERROR {r['error']}", flush=True)
            else:
                c = r.get("compass_365d", {})
                print(f"[{len(results)}/{len(targets)}] {r['sym_side']}: OOS_gen {r['oos_generalizes_count']}/{len(SCENARIOS)} "
                      f"OOS_meanΔ {r['oos_mean_delta']} 365D_Δ {c.get('delta','?')} robust={r['robust']}", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"scenarios": [f"{a}/{b}" for a, b in SCENARIOS], "n": len(results),
               "n_robust": sum(1 for r in results if r.get("robust")), "results": results}, open(OUT, "w"), indent=1)
    # per-scenario aggregate: which IS/OOS best curbs overfit (mean OOS delta, % generalizing)
    print("\n=== SCENARIO AGGREGATE (which window best curbs overfit) ===")
    for sc in [f"{a}/{b}" for a, b in SCENARIOS]:
        ds = [r["scenarios"][sc]["oos_delta"] for r in results if "scenarios" in r and sc in r["scenarios"] and "oos_delta" in r["scenarios"][sc]]
        gen = [1 for r in results if "scenarios" in r and sc in r["scenarios"] and r["scenarios"][sc].get("generalizes")]
        if ds:
            print(f"  IS/OOS {sc}: mean OOS Δ {np.mean(ds):+.2f}pp, {len(gen)}/{len(ds)} generalize ({100*len(gen)/len(ds):.0f}%)")
    print(f"\n[walkforward] robust {sum(1 for r in results if r.get('robust'))}/{len(results)} -> {OUT}")


if __name__ == "__main__":
    main()
