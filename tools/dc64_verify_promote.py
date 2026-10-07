#!/usr/bin/env python
"""dc64_verify_promote.py — verify each greedy winner on 365D + backtest_v12_engine (live-faithful)
and stage the passers for live promotion. RUNS ON SERVERS (needs NPZ + live engine).

Gate per sym_side (winner from SPREADSHEETS/DC64_GREEDY/*_matrix.xlsx):
  1. 30D greedy FINAL > base            (already true by construction)
  2. 365D: cum_ov gain > base_ov gain   (overfit guard)
  3. backtest_v12_engine.run_one parity vs 30D vec  (trade ratio 0.80-1.25, gain <0.5pp & <15%)
     AND live gain > 0
Passers -> data/reports/dc64_promotions.json (sym_side, applied switches, all metrics).
NO live write here. The Mac applies it (tools/dc64_apply_promotions.py) with backup.

Usage (on server):
  ~/miniforge3/bin/python tools/dc64_verify_promote.py --venue crypto --workers 6
  .venv/bin/python tools/dc64_verify_promote.py --venue stocks --workers 6
"""
import os, sys, json, glob, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
import v12_quick_engine as V
from tools import dc_simple_8_sweep as DC
from tools import dc64_greedy as G

OUT = os.path.join(ROOT, "data", "reports", "dc64_promotions.json")


def _reconstruct(xlsx_path):
    """Return (sym_side, base_ov, cum_ov, applied, base_gain30, final_gain30) from a greedy xlsx."""
    import openpyxl
    sym_side = os.path.basename(xlsx_path).split("_bh")[0]
    base_ov, sym, is_long, crypto = G._base_overrides(sym_side, PM)
    lut = {gn: dict(opts) for gn, opts in G._group_options()}
    wb = openpyxl.load_workbook(xlsx_path); ws = wb.active
    cum_ov = dict(base_ov); base_gain = None; final_gain = None
    for r in range(2, ws.max_row + 1):
        sw = ws.cell(r, 1).value; cg = ws.cell(r, 3).value; d = ws.cell(r, 4).value
        if sw == "BASE":
            base_gain = cg; final_gain = cg; continue
        if cg is not None:
            final_gain = cg
        try:
            if sw and "=" in str(sw) and d is not None and float(d) > 1e-9:
                gn, lab = str(sw).split("=", 1)
                if gn in lut and lab in lut[gn]:
                    cum_ov.update(lut[gn][lab])
        except Exception:
            pass
    applied = {k: cum_ov[k] for k in cum_ov if base_ov.get(k) != cum_ov[k]}
    return sym_side, sym, is_long, crypto, base_ov, cum_ov, applied, base_gain, final_gain


def _parity_ok(live, vec):
    if not live.get("valid"):
        return False, f"live invalid {live.get('invalid_reason')}"
    lt = int(live.get("trades") or 0); vt = int(vec.get("trades") or 0)
    if lt == 0 or vt == 0:
        return False, f"zero trades live={lt} vec={vt}"
    ratio = vt / lt
    if not (0.80 <= ratio <= 1.25):
        return False, f"trade ratio {ratio:.2f}"
    lg = float(live.get("gain_pct") or 0); vg = float(vec.get("gain_pct") or 0)
    if abs(lg - vg) > 0.5 and abs(lg - vg) / max(1e-9, abs(lg)) > 0.15:
        return False, f"gain mismatch live {lg:.2f} vec {vg:.2f}"
    return True, "ok"


def verify_one(xlsx_path):
    try:
        sym_side, sym, is_long, crypto, base_ov, cum_ov, applied, bg30, fg30 = _reconstruct(xlsx_path)
    except Exception as e:
        return {"sym_side": os.path.basename(xlsx_path), "pass": False, "reason": f"reconstruct {e}"}
    if not applied:
        return {"sym_side": sym_side, "pass": False, "reason": "no switches promoted (baseline unchanged)"}
    stores = V.load_npz("crypto" if crypto else "tradier", [sym], "2024-01-01")
    npz = stores.get(sym)
    if npz is None or len(npz.get("timestamps", [])) < 100:
        return {"sym_side": sym_side, "pass": False, "reason": "npz missing"}
    # 2. 365D overfit guard
    try:
        b365, _ = DC.eval_gain(npz, sym, is_long, base_ov, 365)
        n365, _ = DC.eval_gain(npz, sym, is_long, cum_ov, 365)
        g_b365 = (b365 or {}).get("gain_pct_2000norm", 0.0); g_n365 = (n365 or {}).get("gain_pct_2000norm", 0.0)
        d365 = g_n365 - g_b365
    except Exception as e:
        return {"sym_side": sym_side, "pass": False, "reason": f"365D {e}"}
    if d365 <= 1e-9:
        return {"sym_side": sym_side, "pass": False, "reason": f"365D delta {d365:.2f}<=0 (overfit)",
                "d30": round(fg30 - bg30, 2), "d365": round(d365, 2)}
    # 3. live-faithful parity via backtest_v12_engine — in a subprocess with a per-sym
    #    timeout so a bad/no-data sym (e.g. an NPZ being rewritten) can't stall a worker.
    import subprocess, tempfile, json as _json
    _tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    _json.dump({k: (v if isinstance(v, (int, float, str, bool)) else str(v)) for k, v in cum_ov.items()}, _tf)
    _tf.close()
    _runner = os.path.join(ROOT, "tools", "_v12_runone.py")
    try:
        _r = subprocess.run([sys.executable, _runner, sym_side, _tf.name, "30"],
                            capture_output=True, text=True, timeout=420)
        live = None
        for _ln in _r.stdout.splitlines():
            if _ln.startswith("V12RESULT "):
                live = _json.loads(_ln[10:]); break
        if live is None:
            return {"sym_side": sym_side, "pass": False, "reason": f"live no-result {(_r.stderr or '')[-120:]}",
                    "d30": round(fg30 - bg30, 2), "d365": round(d365, 2)}
    except subprocess.TimeoutExpired:
        return {"sym_side": sym_side, "pass": False, "reason": "live timeout 420s (likely no-data/rewritten NPZ)",
                "d30": round(fg30 - bg30, 2), "d365": round(d365, 2)}
    finally:
        try: os.unlink(_tf.name)
        except Exception: pass
    vec = {"valid": True, "gain_pct": fg30, "trades": int((n365 or {}).get("trades", 0) or fg30 and 1)}
    # use the 30D vec trades for ratio
    try:
        v30, _ = DC.eval_gain(npz, sym, is_long, cum_ov, 30)
        vec = {"valid": True, "gain_pct": v30.get("gain_pct_2000norm", fg30), "trades": int(v30.get("trades", 0))}
    except Exception:
        pass
    ok, why = _parity_ok(live, vec)
    lg = float(live.get("gain_pct") or 0)
    passed = ok and lg > 0
    return {"sym_side": sym_side, "venue": "crypto" if crypto else "stocks", "pass": bool(passed),
            "reason": why if passed else (f"parity {why}" if not ok else f"live gain {lg:.2f}<=0"),
            "applied": {k: (v if isinstance(v, (int, float, str, bool)) else str(v)) for k, v in applied.items()},
            "base_gain30": round(bg30, 2), "final_gain30": round(fg30, 2), "d30": round(fg30 - bg30, 2),
            "gain365_base": round(g_b365, 2), "gain365_new": round(g_n365, 2), "d365": round(d365, 2),
            "live_gain30": round(lg, 2), "live_trades": int(live.get("trades") or 0),
            "live_sharpe": round(float(live.get("pool_sharpe") or 0), 3)}


def main():
    global PM
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=["crypto", "stocks", "both"], default="both")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    PM, _, _ = DC.load_per_sym_maps()
    xs = sorted(glob.glob(os.path.join(G.OUT_DIR, "*_matrix.xlsx")))
    def isc(p):
        b = os.path.basename(p); bb = b.split("_bh")[0]
        base = bb[:-5] if bb.endswith("_LONG") else bb[:-6]
        return base.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD"))
    if a.venue != "both":
        xs = [p for p in xs if isc(p) == (a.venue == "crypto")]
    if a.limit:
        xs = xs[:a.limit]
    print(f"[verify] {len(xs)} winners venue={a.venue} workers={a.workers}", flush=True)
    results = []
    existing = {}
    if os.path.exists(OUT):
        try:
            existing = {r["sym_side"]: r for r in json.load(open(OUT)).get("results", [])}
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(verify_one, p): p for p in xs}
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception as e:
                r = {"sym_side": os.path.basename(futs[f]), "pass": False, "reason": f"EXC {e}"}
            results.append(r)
            tag = "PASS" if r.get("pass") else "----"
            print(f"[{len(results)}/{len(xs)}] {tag} {r['sym_side']}: {r.get('reason','')} "
                  f"d30={r.get('d30','')} d365={r.get('d365','')} live={r.get('live_gain30','')}", flush=True)
    existing.update({r["sym_side"]: r for r in results})
    allr = list(existing.values())
    npass = sum(1 for r in allr if r.get("pass"))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"generated": __import__("datetime").datetime.utcnow().isoformat(),
                   "n_pass": npass, "n_total": len(allr), "results": allr}, f, indent=1)
    print(f"[verify] DONE {npass}/{len(allr)} PASS -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
